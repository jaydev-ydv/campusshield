"""Campus-local time derivation for ``core.report``.

``occurred_hour`` and ``occurred_dow`` are plain columns rather than generated
ones because ``EXTRACT(... FROM timestamptz)`` is not ``IMMUTABLE`` in
PostgreSQL — it depends on the session ``TimeZone`` — so it cannot legally back
a generated column or an index expression.  The application owns the conversion,
and it must use the campus timezone rather than the server's: a report filed at
21:00 on campus is an evening report no matter where the server sits.

Hotspot detection, the modal-hour band on clusters, and the "night hours" risk
factor all read these two columns, so getting the zone wrong quietly skews every
downstream pattern rather than failing loudly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import ValidationError


def resolve_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - configuration error
        raise RuntimeError(f"unknown CAMPUS_TIMEZONE {name!r}") from exc


def ensure_aware(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC rather than guessing."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def campus_hour_and_dow(occurred_at: datetime, zone_name: str) -> tuple[int, int]:
    """Return ``(hour 0-23, weekday 0-6)`` in campus-local time.

    Weekday is 0 = Monday, matching ``EXTRACT(ISODOW) - 1`` and Python's
    ``weekday()``.  The schema only constrains the range, so the convention has
    to be stated somewhere; it is stated here.
    """
    local = ensure_aware(occurred_at).astimezone(resolve_zone(zone_name))
    return local.hour, local.weekday()


def reject_future(occurred_at: datetime, now: datetime, *, tolerance_seconds: int = 120) -> None:
    """Reject a report of something that has not happened.

    A small tolerance absorbs clock skew between a phone and the server; without
    it a device running two minutes fast cannot file at all.
    """
    delta = (ensure_aware(occurred_at) - ensure_aware(now)).total_seconds()
    if delta > tolerance_seconds:
        raise ValidationError(
            "occurred_at cannot be in the future.",
            details={"fields": {"occurred_at": ["Timestamp is in the future."]}},
        )
