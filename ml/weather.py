"""
Open-Meteo client.

Two endpoints we care about:
  - Forecast API  → today + next 16 days, daily aggregates.
  - Archive API   → historical daily values, used for training data backfill.

No API key needed; calls timeout fast and fail gracefully so a network blip
never breaks the nightly forecast trainer — the trainer just falls back to a
weather-agnostic fit.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Iterable, Optional

from django.db import transaction

from menu.models import RestaurantSettings
from ml.models import WeatherObservation


logger = logging.getLogger(__name__)


OPEN_METEO_FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
OPEN_METEO_ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'

# WMO weather code → "rainy" (0=clear, 51-67=drizzle/rain, 80-82=showers, 95-99=thunder)
_RAINY_CODES = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))

# Default request settings.
_TIMEOUT_SECS = 15
_DAILY_PARAMS = 'temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code'


class WeatherUnavailable(Exception):
    """Raised when we deliberately can't fetch (no lat/lon, no network, bad response)."""


def _coords():
    """Return (lat, lon) from RestaurantSettings or raise WeatherUnavailable."""
    s = RestaurantSettings.load()
    if s.latitude is None or s.longitude is None:
        raise WeatherUnavailable(
            'RestaurantSettings.latitude / .longitude not set — '
            'set them in admin to enable weather-aware forecasting.'
        )
    return float(s.latitude), float(s.longitude)


def _is_rainy(precip_mm: Optional[float], weather_code: Optional[int]) -> bool:
    return (precip_mm or 0) > 0.5 or (weather_code in _RAINY_CODES if weather_code is not None else False)


def _http_get_json(url: str, params: dict) -> dict:
    """Lightweight stdlib GET — avoids pulling `requests` as a dep."""
    qs = urllib.parse.urlencode(params)
    full = f'{url}?{qs}'
    try:
        with urllib.request.urlopen(full, timeout=_TIMEOUT_SECS) as resp:
            if resp.status != 200:
                raise WeatherUnavailable(f'HTTP {resp.status} from {url}')
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise WeatherUnavailable(f'Could not reach {url}: {e}')


def _parse_daily(payload: dict, source: str, lat: float, lon: float) -> list[dict]:
    """Convert Open-Meteo `daily` block to WeatherObservation field dicts."""
    daily = payload.get('daily') or {}
    times = daily.get('time') or []
    tmax = daily.get('temperature_2m_max') or []
    tmin = daily.get('temperature_2m_min') or []
    precip = daily.get('precipitation_sum') or []
    codes = daily.get('weather_code') or []

    rows = []
    for i, t in enumerate(times):
        try:
            d = date.fromisoformat(t)
        except ValueError:
            continue
        p = precip[i] if i < len(precip) else None
        c = codes[i] if i < len(codes) else None
        rows.append({
            'date': d,
            'source': source,
            'temp_max_c': tmax[i] if i < len(tmax) else None,
            'temp_min_c': tmin[i] if i < len(tmin) else None,
            'precipitation_mm': p,
            'weather_code': c,
            'is_rainy': _is_rainy(p, c),
            'latitude': lat,
            'longitude': lon,
        })
    return rows


# ── Public API ───────────────────────────────────────────────────────────

def fetch_forecast(days_ahead: int = 16) -> int:
    """
    Fetch today + next `days_ahead` daily forecasts. Returns rows upserted.
    Raises WeatherUnavailable if config is missing or the API is unreachable.
    """
    lat, lon = _coords()
    payload = _http_get_json(OPEN_METEO_FORECAST_URL, {
        'latitude': lat,
        'longitude': lon,
        'daily': _DAILY_PARAMS,
        'timezone': 'auto',
        'forecast_days': max(1, min(16, days_ahead)),
    })
    rows = _parse_daily(payload, source='forecast', lat=lat, lon=lon)
    return _upsert(rows)


def fetch_historical(start_date: date, end_date: date) -> int:
    """
    Fetch historical actuals between start_date and end_date (inclusive).
    Used by `backfill_weather` and by the nightly task to record yesterday's
    actual once the forecast becomes history.
    """
    lat, lon = _coords()
    if end_date < start_date:
        return 0
    payload = _http_get_json(OPEN_METEO_ARCHIVE_URL, {
        'latitude': lat,
        'longitude': lon,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'daily': _DAILY_PARAMS,
        'timezone': 'auto',
    })
    rows = _parse_daily(payload, source='actual', lat=lat, lon=lon)
    return _upsert(rows)


@transaction.atomic
def _upsert(rows: Iterable[dict]) -> int:
    """
    Insert or update by date. Forecasts can be overwritten by later forecasts;
    actuals overwrite earlier forecasts (more reliable signal).
    """
    n = 0
    for r in rows:
        # An actual always wins over an existing forecast; a fresh forecast
        # only updates if there isn't already an actual for that date.
        existing = WeatherObservation.objects.filter(date=r['date']).first()
        if existing and existing.source == 'actual' and r['source'] == 'forecast':
            continue
        WeatherObservation.objects.update_or_create(date=r['date'], defaults=r)
        n += 1
    return n


def refresh_weather() -> dict:
    """
    Combined nightly refresh: pull yesterday's actual + next 14 days of forecast.
    Returns a small stats dict — useful for ModelRun.error / logs.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    stats = {'actuals': 0, 'forecast': 0}
    try:
        stats['actuals'] = fetch_historical(yesterday, yesterday)
    except WeatherUnavailable as e:
        logger.warning('Historical fetch skipped: %s', e)
    try:
        stats['forecast'] = fetch_forecast(14)
    except WeatherUnavailable as e:
        logger.warning('Forecast fetch skipped: %s', e)
    return stats


def weather_available() -> bool:
    """Cheap check used by the trainer to decide whether to load regressors."""
    return WeatherObservation.objects.exists()
