"""
Per-menu-item demand forecast.

Approach:
  - Aggregate paid orders → daily quantity per menu item.
  - Optionally join `WeatherObservation` to expose temp_max_c and
    precipitation_mm as Prophet extra regressors.
  - For each item with enough history:
      a) MA-7 baseline       (no model)
      b) Prophet no weather  (always tried when Prophet is available)
      c) Prophet w/ weather  (only when weather covers train + holdout)
    Per-item backtest picks the winner by held-out MAE.
  - Writes the 14-day forward forecast to DemandForecast.
"""

from datetime import date, timedelta
import logging
from statistics import mean
from typing import Optional

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from menu.models import MenuItem, OrderItem
from ml import calendar_features, fallbacks, gates
from ml.models import DemandForecast, WeatherObservation
from ml.trainers._runner import model_run

logger = logging.getLogger(__name__)

HORIZON_DAYS = 14
BACKTEST_TAIL_DAYS = 14

# Regressors we pass to Prophet when weather is available. Only numerics —
# is_rainy / weather_code are categorical and add little after precipitation.
WEATHER_REGRESSORS = ('temp_max_c', 'precipitation_mm')

# Always-on calendar regressor — pure date math, no external dep.
CALENDAR_REGRESSORS = ('is_payday_window',)


def _try_import_prophet():
    try:
        from prophet import Prophet  # noqa: WPS433
        return Prophet
    except Exception as e:
        logger.warning('prophet not available (%s) — using seasonal-naive', e)
        return None


def _weather_dataframe():
    """
    Return a pandas DataFrame indexed by `ds` (date) with WEATHER_REGRESSORS
    columns, or None if pandas isn't available or there's no weather data.
    """
    try:
        import pandas as pd  # noqa: WPS433
    except Exception:
        return None

    rows = list(
        WeatherObservation.objects.values('date', *WEATHER_REGRESSORS)
    )
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.rename(columns={'date': 'ds'})
    # Drop rows missing any regressor — Prophet would error on NaN.
    df = df.dropna(subset=list(WEATHER_REGRESSORS))
    if df.empty:
        return None
    df['ds'] = pd.to_datetime(df['ds'])
    return df


def _series_for_item(menu_item_id):
    """Return list of (date, qty) tuples ordered by date, gap-filled with zeros."""
    rows = (
        OrderItem.objects
        .filter(menu_item_id=menu_item_id, order__status='paid')
        .values('order__created_at__date')
        .annotate(qty=Sum('quantity'))
        .order_by('order__created_at__date')
    )
    if not rows:
        return []
    days = {r['order__created_at__date']: float(r['qty']) for r in rows}
    start = min(days)
    end = max(days)
    out = []
    cur = start
    while cur <= end:
        out.append((cur, days.get(cur, 0.0)))
        cur += timedelta(days=1)
    return out


def _mae(predictions, actuals):
    if not predictions or not actuals or len(predictions) != len(actuals):
        return float('inf')
    return sum(abs(p - a) for p, a in zip(predictions, actuals)) / len(predictions)


def _moving_avg_baseline_forecast(series, horizon):
    """Predict horizon days as the last 7-day mean."""
    if not series:
        return [0.0] * horizon
    last7 = [q for _, q in series[-7:]]
    avg = mean(last7) if last7 else 0.0
    return [avg] * horizon


def _prophet_forecast(series, horizon, Prophet, weather_df=None, holidays_df=None):
    """
    Fit Prophet on `series`, predict `horizon` future days.

    Always includes:
      - Kenyan public holidays via Prophet's `holidays=` argument
        (when a holidays_df is supplied).
      - `is_payday_window` as an additional regressor (pure date math).

    Optionally includes:
      - Weather regressors (temp_max_c, precipitation_mm) when weather_df
        covers both training dates AND all horizon dates. Falls back to
        weather-free if horizon coverage is incomplete.

    Returns (p50_list, p90_list) or raises if Prophet itself fails.
    """
    import pandas as pd
    df = pd.DataFrame([{'ds': d, 'y': q} for d, q in series])
    df['ds'] = pd.to_datetime(df['ds'])
    calendar_features.add_payday_column(df)

    use_weather = False
    if weather_df is not None:
        merged = df.merge(weather_df, on='ds', how='left')
        if not merged[list(WEATHER_REGRESSORS)].isna().any().any():
            df = merged
            use_weather = True

    m = Prophet(
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=False,
        interval_width=0.8,
        holidays=holidays_df,
    )
    for r in CALENDAR_REGRESSORS:
        m.add_regressor(r)
    if use_weather:
        for r in WEATHER_REGRESSORS:
            m.add_regressor(r)
    m.fit(df)

    future = m.make_future_dataframe(periods=horizon)
    calendar_features.add_payday_column(future)
    if use_weather:
        future = future.merge(weather_df, on='ds', how='left')
        if future[list(WEATHER_REGRESSORS)].isna().any().any():
            # Future weather missing for some horizon days — drop weather
            # regressors and refit. Cheaper than a half-broken model.
            return _prophet_forecast(
                series, horizon, Prophet,
                weather_df=None, holidays_df=holidays_df,
            )
    fc = m.predict(future).tail(horizon)
    p50 = fc['yhat'].clip(lower=0).tolist()
    p90 = fc['yhat_upper'].clip(lower=0).tolist()
    return p50, p90


def _backtest_item(series, Prophet, weather_df, holidays_df):
    """
    Hold out the last BACKTEST_TAIL_DAYS days; return MAE for each strategy.
    Both Prophet variants include holidays + pay-cycle (cheap, always-on);
    the comparison is over whether weather regressors are worth adding.
    """
    if len(series) < BACKTEST_TAIL_DAYS * 2:
        return None

    train = series[:-BACKTEST_TAIL_DAYS]
    holdout = [q for _, q in series[-BACKTEST_TAIL_DAYS:]]

    out = {
        'baseline': _mae(_moving_avg_baseline_forecast(train, BACKTEST_TAIL_DAYS), holdout),
        'ml_no_weather': float('inf'),
        'ml_weather': float('inf'),
    }
    if Prophet is None:
        return out

    try:
        p50, _ = _prophet_forecast(
            train, BACKTEST_TAIL_DAYS, Prophet,
            weather_df=None, holidays_df=holidays_df,
        )
        out['ml_no_weather'] = _mae(p50, holdout)
    except Exception as e:
        logger.warning('prophet (no-weather) backtest failed: %s', e)

    if weather_df is not None:
        try:
            p50, _ = _prophet_forecast(
                train, BACKTEST_TAIL_DAYS, Prophet,
                weather_df=weather_df, holidays_df=holidays_df,
            )
            out['ml_weather'] = _mae(p50, holdout)
        except Exception as e:
            logger.warning('prophet (weather) backtest failed: %s', e)

    return out


def train():
    """Run a full nightly forecast cycle. Writes to DemandForecast."""
    with model_run('forecast') as run:
        ready, info = gates.forecast_ready()
        run.rows_used = info['orders']

        if not ready:
            baseline_rows = fallbacks.forecast_baseline(HORIZON_DAYS)
            _replace_forecast_window(baseline_rows, source='baseline')
            run.rows_written = len(baseline_rows)
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = (
                f"Not enough data: {info['orders']} orders, "
                f"{info['days_with_orders']}/{info['need_days']} days. Using baseline."
            )
            return

        Prophet = _try_import_prophet()
        weather_df = _weather_dataframe()
        weather_note = (
            f"weather: {len(weather_df)} day(s) of coverage"
            if weather_df is not None else "weather: none (skipping regressors)"
        )

        # Build holidays once for the full data + horizon span; Prophet
        # will only apply rows that fall within each fit's training window.
        cal = calendar_features.calendar_status()
        holidays_df = calendar_features.holidays_dataframe(cal['start'], cal['end'])
        cal_note = (
            f"calendar: {cal['holidays_in_range']} holiday(s) "
            f"({'holidays lib' if cal['using_lib'] else 'fallback table'}) "
            f"+ payday-window regressor"
        )

        all_rows = []
        winners = {'baseline': 0, 'ml_no_weather': 0, 'ml_weather': 0}
        winning_maes = []
        baseline_maes = []
        today = timezone.localdate()

        for item in MenuItem.objects.filter(is_available=True).iterator():
            item_ready, _ = gates.item_forecast_ready(item.pk)
            series = _series_for_item(item.pk)
            if not series:
                continue

            bt = (
                _backtest_item(series, Prophet, weather_df, holidays_df)
                if item_ready else None
            )
            choice, choice_mae = _pick_strategy(bt)

            try:
                p50, p90, source = _forecast_with(
                    choice, series, HORIZON_DAYS, Prophet, weather_df, holidays_df,
                )
            except Exception as e:
                logger.warning('forecast failed for item %s, falling back: %s', item.pk, e)
                base = _moving_avg_baseline_forecast(series, HORIZON_DAYS)
                p50 = base
                p90 = [v * 1.4 for v in base]
                source = 'baseline'
                choice = 'baseline'

            winners[choice] = winners.get(choice, 0) + 1
            if bt is not None:
                baseline_maes.append(bt['baseline'])
                if choice_mae != float('inf'):
                    winning_maes.append(choice_mae)

            for d_ahead, (median, upper) in enumerate(zip(p50, p90), start=1):
                all_rows.append({
                    'menu_item_id': item.pk,
                    'date': today + timedelta(days=d_ahead),
                    'hour': None,
                    'qty_p50': round(float(median), 2),
                    'qty_p90': round(float(upper), 2),
                    'source': source,
                })

        _replace_forecast_window(all_rows, today=today)
        run.rows_written = len(all_rows)
        if winning_maes:
            run.metric_name = 'mae'
            run.metric_value = mean(winning_maes)
            run.baseline_value = mean(baseline_maes) if baseline_maes else None
        run.error = (
            f"{cal_note}. {weather_note}. Strategy wins: "
            f"baseline={winners['baseline']}, "
            f"ml_no_weather={winners['ml_no_weather']}, "
            f"ml_weather={winners['ml_weather']}."
        )


def _pick_strategy(bt: Optional[dict]):
    """Return (name, mae). Defaults to baseline when no backtest available."""
    if bt is None:
        return 'baseline', float('inf')
    options = [
        ('ml_weather', bt['ml_weather']),
        ('ml_no_weather', bt['ml_no_weather']),
        ('baseline', bt['baseline']),
    ]
    name, mae = min(options, key=lambda x: x[1])
    if mae == float('inf'):
        return 'baseline', bt['baseline']
    return name, mae


def _forecast_with(choice, series, horizon, Prophet, weather_df, holidays_df):
    """Run the picked strategy and return (p50, p90, source_label)."""
    if choice == 'ml_weather' and Prophet is not None and weather_df is not None:
        p50, p90 = _prophet_forecast(
            series, horizon, Prophet,
            weather_df=weather_df, holidays_df=holidays_df,
        )
        return p50, p90, 'ml'
    if choice == 'ml_no_weather' and Prophet is not None:
        p50, p90 = _prophet_forecast(
            series, horizon, Prophet,
            weather_df=None, holidays_df=holidays_df,
        )
        return p50, p90, 'ml'
    base = _moving_avg_baseline_forecast(series, horizon)
    return base, [v * 1.4 for v in base], 'baseline'


@transaction.atomic
def _replace_forecast_window(rows, today=None, source=None):
    """Replace forward forecasts for the window; keep historical rows for audit."""
    today = today or timezone.localdate()
    DemandForecast.objects.filter(date__gt=today).delete()
    objs = [
        DemandForecast(
            menu_item_id=r['menu_item_id'],
            date=r['date'],
            hour=r.get('hour'),
            qty_p50=r['qty_p50'],
            qty_p90=r['qty_p90'],
            source=source or r.get('source', 'ml'),
        )
        for r in rows
    ]
    DemandForecast.objects.bulk_create(objs, batch_size=500)
