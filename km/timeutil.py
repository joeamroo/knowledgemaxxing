"""Timestamp conversions.

Sources use wildly different epochs:
- Chrome History SQLite: WebKit epoch, microseconds since 1601-01-01 UTC
- Google Takeout time_usec: microseconds since Unix epoch
- Extension CSV exports: seconds, millis, ISO strings, US formats
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# Magnitude bounds for epoch inference (values between 1990 and 2100 in each unit)
_SEC_LO, _SEC_HI = 631_152_000, 4_102_444_800
_MS_LO, _MS_HI = _SEC_LO * 1_000, _SEC_HI * 1_000
_US_LO, _US_HI = _SEC_LO * 1_000_000, _SEC_HI * 1_000_000
# WebKit micros for 1990..2100 (offset by 11644473600 seconds from Unix epoch)
_WEBKIT_OFFSET_US = 11_644_473_600 * 1_000_000
_WK_LO, _WK_HI = _US_LO + _WEBKIT_OFFSET_US, _US_HI + _WEBKIT_OFFSET_US

_STRING_FORMATS = (
    "%a %b %d %H:%M:%S %z %Y",  # Twitter created_at
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%b %d, %Y %I:%M:%S %p",
    "%b %d, %Y",
)


def webkit_to_dt(value: int | float) -> datetime:
    """Convert WebKit epoch microseconds (since 1601-01-01 UTC) to datetime."""
    return _WEBKIT_EPOCH + timedelta(microseconds=int(value))


def usec_to_dt(value: int | float) -> datetime:
    """Convert microseconds since Unix epoch to datetime."""
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)


def _from_number(n: float) -> datetime | None:
    if _SEC_LO <= n < _SEC_HI:
        return datetime.fromtimestamp(n, tz=timezone.utc)
    if _MS_LO <= n < _MS_HI:
        return datetime.fromtimestamp(n / 1_000, tz=timezone.utc)
    if _US_LO <= n < _US_HI:
        return usec_to_dt(n)
    if _WK_LO <= n < _WK_HI:
        return webkit_to_dt(n)
    return None


def infer_timestamp(value: object) -> datetime | None:
    """Best-effort timestamp parse for heterogeneous exports.

    Numbers are interpreted by magnitude (seconds, millis, Unix micros,
    WebKit micros). Strings try ISO 8601 then common export formats.
    Returns timezone-aware UTC datetimes, or None when unparseable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_number(float(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return _from_number(float(s))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in _STRING_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None
