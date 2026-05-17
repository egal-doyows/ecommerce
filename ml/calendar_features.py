"""
Calendar-derived features for the forecast trainer.

Two things live here:

  1. Kenyan public holidays  — passed to Prophet via `holidays=`.
     Uses the `holidays` library when available (handles Eid lunar drift
     correctly). Falls back to a hardcoded multi-year table so the
     trainer keeps running without the dep.

  2. Pay-cycle window         — a binary regressor, 1 when day-of-month
     falls in the typical Kenyan pay/spend cluster (≤5 or ≥25).
     Derivable from the date alone, so it's always available.

Both are cheap, predictive, and have no external dependencies.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional


logger = logging.getLogger(__name__)


PAYDAY_LOW_DAY = 5    # 1st–5th capture early-month spend after end-of-month salary
PAYDAY_HIGH_DAY = 25  # 25th–end capture the actual end-of-month payday


def _try_import_holidays_lib():
    try:
        import holidays as _holidays  # noqa: WPS433
        return _holidays
    except Exception as e:
        logger.info('holidays lib not installed (%s) — using hardcoded fallback', e)
        return None


# ── Hardcoded fallback (covers core dates the holidays lib would return) ──
# Eid dates shift with the lunar calendar; the dates below are the
# observed/gazetted Kenyan public holidays. Source: Kenya gazette notices.
# Update this table once a year if the holidays library isn't installed.
_FALLBACK_KE_HOLIDAYS = {
    # 2024
    date(2024, 1, 1):   "New Year's Day",
    date(2024, 3, 29):  'Good Friday',
    date(2024, 4, 1):   'Easter Monday',
    date(2024, 4, 10):  'Eid al-Fitr',
    date(2024, 5, 1):   'Labour Day',
    date(2024, 6, 1):   'Madaraka Day',
    date(2024, 6, 17):  'Eid al-Adha',
    date(2024, 10, 10): 'Huduma Day',
    date(2024, 10, 20): 'Mashujaa Day',
    date(2024, 12, 12): 'Jamhuri Day',
    date(2024, 12, 25): 'Christmas Day',
    date(2024, 12, 26): 'Boxing Day',
    # 2025
    date(2025, 1, 1):   "New Year's Day",
    date(2025, 4, 18):  'Good Friday',
    date(2025, 4, 21):  'Easter Monday',
    date(2025, 3, 31):  'Eid al-Fitr',
    date(2025, 5, 1):   'Labour Day',
    date(2025, 6, 1):   'Madaraka Day',
    date(2025, 6, 7):   'Eid al-Adha',
    date(2025, 10, 10): 'Huduma Day',
    date(2025, 10, 20): 'Mashujaa Day',
    date(2025, 12, 12): 'Jamhuri Day',
    date(2025, 12, 25): 'Christmas Day',
    date(2025, 12, 26): 'Boxing Day',
    # 2026
    date(2026, 1, 1):   "New Year's Day",
    date(2026, 4, 3):   'Good Friday',
    date(2026, 4, 6):   'Easter Monday',
    date(2026, 3, 20):  'Eid al-Fitr',
    date(2026, 5, 1):   'Labour Day',
    date(2026, 5, 27):  'Eid al-Adha',
    date(2026, 6, 1):   'Madaraka Day',
    date(2026, 10, 10): 'Huduma Day',
    date(2026, 10, 20): 'Mashujaa Day',
    date(2026, 12, 12): 'Jamhuri Day',
    date(2026, 12, 25): 'Christmas Day',
    date(2026, 12, 26): 'Boxing Day',
    # 2027
    date(2027, 1, 1):   "New Year's Day",
    date(2027, 3, 26):  'Good Friday',
    date(2027, 3, 29):  'Easter Monday',
    date(2027, 3, 10):  'Eid al-Fitr',
    date(2027, 5, 1):   'Labour Day',
    date(2027, 5, 17):  'Eid al-Adha',
    date(2027, 6, 1):   'Madaraka Day',
    date(2027, 10, 10): 'Huduma Day',
    date(2027, 10, 20): 'Mashujaa Day',
    date(2027, 12, 12): 'Jamhuri Day',
    date(2027, 12, 25): 'Christmas Day',
    date(2027, 12, 26): 'Boxing Day',
}


def _ke_holiday_map(start: date, end: date) -> dict[date, str]:
    """Return {date: name} for Kenya holidays in [start, end]."""
    lib = _try_import_holidays_lib()
    if lib is not None:
        years = range(start.year, end.year + 1)
        try:
            ke = lib.Kenya(years=list(years))
            return {d: name for d, name in ke.items() if start <= d <= end}
        except Exception as e:
            logger.warning('holidays lib failed (%s) — using hardcoded fallback', e)
    return {d: name for d, name in _FALLBACK_KE_HOLIDAYS.items() if start <= d <= end}


# ── Public API ───────────────────────────────────────────────────────────

def holidays_dataframe(start: date, end: date):
    """
    Return a Prophet-compatible holidays DataFrame for the date range,
    or None if pandas isn't installed.
    """
    try:
        import pandas as pd  # noqa: WPS433
    except Exception:
        return None
    rows = [
        {'holiday': name, 'ds': pd.to_datetime(d)}
        for d, name in _ke_holiday_map(start, end).items()
    ]
    if not rows:
        return None
    return pd.DataFrame(rows)


def is_payday_window(d: date) -> int:
    """1 when `d` falls in the typical Kenyan pay/spend cluster, else 0."""
    return int(d.day <= PAYDAY_LOW_DAY or d.day >= PAYDAY_HIGH_DAY)


def add_payday_column(df, ds_col: str = 'ds'):
    """
    Add an `is_payday_window` int column to a pandas DataFrame in place
    (and return it). Works for both training and future frames.
    """
    df['is_payday_window'] = df[ds_col].dt.day.apply(
        lambda day: int(day <= PAYDAY_LOW_DAY or day >= PAYDAY_HIGH_DAY)
    )
    return df


def calendar_status(start: Optional[date] = None, end: Optional[date] = None) -> dict:
    """Snapshot for ml_status — how many holidays and which source."""
    today = date.today()
    start = start or today.replace(month=1, day=1)
    end = end or (today + timedelta(days=365))
    using_lib = _try_import_holidays_lib() is not None
    n = len(_ke_holiday_map(start, end))
    return {
        'using_lib': using_lib,
        'holidays_in_range': n,
        'start': start,
        'end': end,
    }
