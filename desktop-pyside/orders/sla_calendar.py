"""Mercado Libre CBT SLA Working Days & Public Holidays Calendar Engine.

Provides accurate handling deadline computation for cross-border sellers:
- Excludes Saturdays and Sundays by default.
- Excludes Chinese statutory public holidays (CBT origin first-mile carrier hubs).
- Accounts for official adjusted working days (weekend shift workdays).
- Supports dynamic holiday exemptions via data/sla_exemptions.json.
- Accommodates listing custom manufacturing/handling lead times.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Official Chinese Statutory Holidays (CBT Origin logistics hubs)
# Covers 2025, 2026, 2027
_CHINESE_PUBLIC_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31),
    date(2025, 2, 1), date(2025, 2, 2), date(2025, 2, 3), date(2025, 2, 4),
    date(2025, 4, 4), date(2025, 4, 5), date(2025, 4, 6),
    date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 3), date(2025, 5, 4), date(2025, 5, 5),
    date(2025, 5, 31), date(2025, 6, 1), date(2025, 6, 2),
    date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3), date(2025, 10, 4),
    date(2025, 10, 5), date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8),

    # 2026
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3),
    date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 21), date(2026, 2, 22), date(2026, 2, 23),
    date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
    date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4), date(2026, 5, 5),
    date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21),
    date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27),
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4),
    date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),

    # 2027
    date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 3),
    date(2027, 2, 5), date(2027, 2, 6), date(2027, 2, 7), date(2027, 2, 8),
    date(2027, 2, 9), date(2027, 2, 10), date(2027, 2, 11), date(2027, 2, 12),
    date(2027, 4, 4), date(2027, 4, 5), date(2027, 4, 6),
    date(2027, 5, 1), date(2027, 5, 2), date(2027, 5, 3),
    date(2027, 6, 9), date(2027, 6, 10), date(2027, 6, 11),
    date(2027, 9, 15), date(2027, 9, 16), date(2027, 9, 17),
    date(2027, 10, 1), date(2027, 10, 2), date(2027, 10, 3), date(2027, 10, 4),
    date(2027, 10, 5), date(2027, 10, 6), date(2027, 10, 7),
}

# Statutory Adjusted Working Days (Weekend Days that are converted into Official Working Days)
_CHINESE_WORKDAY_SHIFTS: set[date] = {
    # 2025
    date(2025, 1, 26),
    date(2025, 2, 8),
    date(2025, 4, 27),
    date(2025, 9, 28),
    date(2025, 10, 11),

    # 2026
    date(2026, 1, 4),
    date(2026, 2, 14),
    date(2026, 2, 28),
    date(2026, 4, 26),
    date(2026, 5, 9),
    date(2026, 9, 27),
    date(2026, 10, 10),

    # 2027
    date(2027, 2, 14),
    date(2027, 2, 20),
    date(2027, 4, 25),
    date(2027, 5, 8),
    date(2027, 9, 19),
    date(2027, 10, 10),
}


def _load_custom_exemptions() -> set[date]:
    """Load additional dynamic holiday or platform exemption dates from local config."""
    custom_dates: set[date] = set()
    try:
        from engine.crypto import get_data_dir
        path = get_data_dir() / "sla_exemptions.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    try:
                        d = date.fromisoformat(str(item).strip())
                        custom_dates.add(d)
                    except Exception:
                        pass
    except Exception as e:
        logger.debug("Failed to read sla_exemptions.json: %s", e)
    return custom_dates


def is_cbt_business_day(target_date: date) -> bool:
    """Determine whether a given date is considered an official business day for Mercado Libre CBT.
    
    1. If in statutory adjusted workdays (weekend shifts): True.
    2. If weekend (Saturday or Sunday): False.
    3. If in public holidays or platform exemptions: False.
    4. Otherwise: True.
    """
    if target_date in _CHINESE_WORKDAY_SHIFTS:
        return True
    if target_date.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    if target_date in _CHINESE_PUBLIC_HOLIDAYS:
        return False
    custom_exemptions = _load_custom_exemptions()
    if target_date in custom_exemptions:
        return False
    return True


def add_cbt_business_days(start_dt: datetime, business_days: int = 3) -> datetime:
    """Add a specified number of official CBT business days to a starting datetime.
    
    Accurately skips Saturdays, Sundays, and statutory public holidays.
    Preserves exact time-of-day of the source timestamp.
    """
    if business_days <= 0:
        return start_dt

    curr = start_dt
    days_counted = 0
    max_iterations = 120  # Safeguard against infinite loops

    while days_counted < business_days and max_iterations > 0:
        curr += timedelta(days=1)
        max_iterations -= 1
        if is_cbt_business_day(curr.date()):
            days_counted += 1

    return curr


def compute_order_sla_deadline(
    created_dt_or_ts: datetime | float | str,
    handling_days: int = 3,
    manufacturing_days: int = 0,
) -> datetime:
    """Calculate the definitive Mercado Libre CBT handling/shipping deadline.
    
    Defaults to 3 business days, plus any listing manufacturing/custom lead time.
    """
    if isinstance(created_dt_or_ts, (int, float)):
        base_dt = datetime.fromtimestamp(float(created_dt_or_ts), tz=timezone.utc)
    elif isinstance(created_dt_or_ts, str):
        clean_val = str(created_dt_or_ts).strip().replace("Z", "+00:00")
        try:
            base_dt = datetime.fromisoformat(clean_val)
        except Exception:
            base_dt = datetime.now(timezone.utc)
    elif isinstance(created_dt_or_ts, datetime):
        base_dt = created_dt_or_ts
    else:
        base_dt = datetime.now(timezone.utc)

    total_bdays = max(handling_days + manufacturing_days, 1)
    return add_cbt_business_days(base_dt, total_bdays)
