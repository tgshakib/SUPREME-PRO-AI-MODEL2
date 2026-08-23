"""Per-user timezone helpers.

Each user can share their location via the /timezone command. The bot
uses TimezoneFinder to detect the IANA timezone name (e.g. 'Asia/Dhaka')
from the GPS coordinates and stores it on the users.tz column.

All signal timestamps go through `format_for_user(uid, fmt)` so each
member sees the time in their own local zone. Default = UTC.

Time display format: HH:MM:SS UTC+N  (UTC offset number only, no country name)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import database as db

try:
    import pytz
    _PYTZ_OK = True
except Exception as e:
    print(f"[tz_utils] pytz import failed: {e}")
    pytz = None
    _PYTZ_OK = False

try:
    from timezonefinder import TimezoneFinder
    _TF_OK = True
    _TF = TimezoneFinder()
except Exception as e:
    print(f"[tz_utils] timezonefinder import failed: {e}")
    _TF = None
    _TF_OK = False


def detect_tz(lat: float, lng: float) -> Optional[str]:
    """Return an IANA timezone name for given coordinates, or None."""
    if not _TF_OK:
        return None
    try:
        return _TF.timezone_at(lng=lng, lat=lat)
    except Exception as e:
        print(f"[tz_utils] detect_tz error: {e}")
        return None


def get_user_tz(user_id: int) -> str:
    """Return the user's stored IANA timezone, or 'UTC' as fallback."""
    try:
        tz = db.get_user_tz(user_id)
        return tz or "UTC"
    except Exception:
        return "UTC"


def _utc_offset_str(iana_tz: str) -> str:
    """Convert IANA timezone name to UTC+N / UTC-N string.

    Returns e.g. 'UTC+6', 'UTC-5', 'UTC+5:30', 'UTC' for zero offset.
    This shows ONLY the numeric offset — no country or city name.
    """
    if not _PYTZ_OK or pytz is None:
        return "UTC"
    try:
        tz = pytz.timezone(iana_tz)
        now = datetime.now(tz)
        offset = now.utcoffset()
        if offset is None:
            return "UTC"
        total_seconds = int(offset.total_seconds())
        if total_seconds == 0:
            return "UTC"
        sign = "+" if total_seconds >= 0 else "-"
        abs_sec = abs(total_seconds)
        hours   = abs_sec // 3600
        minutes = (abs_sec % 3600) // 60
        if minutes == 0:
            return f"UTC{sign}{hours}"
        return f"UTC{sign}{hours}:{minutes:02d}"
    except Exception:
        return "UTC"


def now_for_user(user_id: int) -> datetime:
    tz = get_user_tz(user_id)
    if not _PYTZ_OK or pytz is None:
        return datetime.utcnow()
    try:
        return datetime.now(pytz.timezone(tz))
    except Exception:
        return datetime.utcnow()


def format_for_user(user_id: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_for_user(user_id).strftime(fmt)


def short_time_for_user(user_id: int) -> str:
    """Return 'HH:MM:SS UTC+N' — numeric UTC offset only, real-time clock.

    No country name, no city, no timezone abbreviation.
    Example: '14:30:55 UTC+6'  or  '09:15:03 UTC-5'  or  '00:00:01 UTC'
    """
    tz_name = get_user_tz(user_id)
    try:
        local_time = now_for_user(user_id)
        time_str   = local_time.strftime("%H:%M:%S")
        offset_str = _utc_offset_str(tz_name)
        return f"{time_str} {offset_str}"
    except Exception:
        utc_time = datetime.utcnow()
        return utc_time.strftime("%H:%M:%S UTC")


def next_candle_time_for_user(user_id: int) -> str:
    """Return the next 1-minute candle start time in user's timezone.

    Format: 'HH:MM +NN' — no seconds, numeric offset only.
    Used for binary signal EXECUTE NOW so users always see the candle
    they will enter (next minute boundary), not the current second.

    Example: pressed at 16:08:45 UTC+6 → returns '16:09 UTC+6'
    """
    tz_name = get_user_tz(user_id)
    try:
        local_time = now_for_user(user_id)
        next_min   = local_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        time_str   = next_min.strftime("%H:%M")
        offset_str = _utc_offset_str(tz_name)
        if offset_str.startswith(("UTC+", "UTC-")):
            sign = offset_str[3]
            value = offset_str[4:]
            if ":" in value:
                hours, minutes = value.split(":", 1)
                offset_str = f"{sign}{int(hours):02d}:{minutes}"
            else:
                offset_str = f"{sign}{int(value):02d}"
        return f"{time_str} {offset_str}"
    except Exception:
        utc_time = datetime.utcnow()
        next_min = utc_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return next_min.strftime("%H:%M UTC")
