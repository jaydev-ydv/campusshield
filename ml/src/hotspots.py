"""Pattern surfacing: `detect_hotspots()`.

A hotspot is a controlled location whose report rate over a recent window is
elevated. It describes a **recurring signal**, not a judgement about any
individual report — nothing here decides whether a report is true, and nothing
here ranks reporters.

Inputs are the four the project specifies: controlled location, time, category,
and recurrence. `HotspotObservation` deliberately has no reporter field, so the
prohibition on using identity or `reporter_relationship` is structural rather
than a rule someone has to remember.

**Not evaluated.** Clustering quality cannot be assessed without real reports at
real locations, and there are none — `core.campus_location` is empty pending the
coordinate survey. This is the interface and a working implementation, delivered
so the backend can call it later; no detection accuracy is claimed.

Leaflet, the map, and the authority dashboard are explicitly out of scope here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class HotspotObservation:
    """A report as the pattern layer is allowed to see it.

    No reporter id, no reporter_relationship, no credibility field — by
    construction, not by convention.
    """

    report_id: str
    location_id: int
    occurred_at: datetime
    category_code: str
    # 0-23 campus-local, as `core.report.occurred_hour` stores it.
    occurred_hour: int | None = None


@dataclass(frozen=True, slots=True)
class Hotspot:
    location_id: int
    window_start: datetime
    window_end: datetime
    report_count: int
    density_per_week: float
    dominant_category: str | None
    dominant_hour_band: str | None
    # Whether this may be shown on the public map, per the k-anonymity threshold.
    publishable: bool
    report_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "report_count": self.report_count,
            "density_per_week": round(self.density_per_week, 3),
            "dominant_category": self.dominant_category,
            "dominant_hour_band": self.dominant_hour_band,
            "publishable": self.publishable,
        }


HOUR_BANDS: tuple[tuple[str, int, int], ...] = (
    ("early_morning", 5, 8),
    ("morning", 9, 12),
    ("afternoon", 13, 16),
    ("evening", 17, 20),
    ("night", 21, 23),
    ("late_night", 0, 4),
)


def hour_band(hour: int) -> str:
    for name, start, end in HOUR_BANDS:
        if start <= hour <= end:
            return name
    return "late_night"


def detect_hotspots(
    observations: list[HotspotObservation],
    *,
    now: datetime | None = None,
    window_days: int = 30,
    min_reports: int = 3,
    min_reports_for_publication: int = 3,
) -> list[Hotspot]:
    """Locations with an elevated report rate in the recent window.

    `min_reports` is a floor, not a statistical test. With no baseline rate for a
    campus, "three reports at one place in a month" is a defensible signal to
    show a human; calling it statistically significant would not be. Once real
    data exists this should become a rate comparison against the location's own
    history, which is what `intervention.impact_measurement` already does for
    before/after.
    """
    if not observations:
        return []

    now = now or max(o.occurred_at for o in observations)
    window_start = now - timedelta(days=window_days)

    by_location: dict[int, list[HotspotObservation]] = defaultdict(list)
    for observation in observations:
        if window_start <= observation.occurred_at <= now:
            by_location[observation.location_id].append(observation)

    hotspots: list[Hotspot] = []
    for location_id, found in by_location.items():
        if len(found) < min_reports:
            continue

        categories = Counter(o.category_code for o in found)
        hours = [o.occurred_hour for o in found if o.occurred_hour is not None]
        bands = Counter(hour_band(h) for h in hours)

        hotspots.append(
            Hotspot(
                location_id=location_id,
                window_start=window_start,
                window_end=now,
                report_count=len(found),
                density_per_week=len(found) / (window_days / 7),
                dominant_category=categories.most_common(1)[0][0] if categories else None,
                dominant_hour_band=bands.most_common(1)[0][0] if bands else None,
                # Mirrors the k-anonymity threshold on analytics.v_public_safety_map.
                # Below k, an "aggregate" can identify both the incident and the
                # person who reported it to anyone who was nearby.
                publishable=len(found) >= min_reports_for_publication,
                report_ids=[o.report_id for o in found],
            )
        )

    hotspots.sort(key=lambda h: (h.report_count, h.density_per_week), reverse=True)
    return hotspots


def evaluation_strategy() -> dict[str, Any]:
    """How hotspot detection would be evaluated once real data exists."""
    return {
        "status": "not_evaluated",
        "reason": (
            "No real reports at real campus locations exist — core.campus_location is "
            "empty pending the coordinate survey. Clustering quality cannot be assessed "
            "against data that does not exist."
        ),
        "planned_method": {
            "ground_truth": (
                "Campus security confirms, for a set of flagged locations, whether the "
                "flag corresponded to a real recurring problem."
            ),
            "metrics": [
                "precision of flagged hotspots against security's assessment",
                "lead time: how far ahead of an escalation the flag appeared",
                "stability: does the same location keep surfacing across windows, or "
                "is the detector reacting to noise",
            ],
            "counterfactual": (
                "Compare against a naive 'most reports overall' ranking. If the "
                "detector adds nothing over counting, it is not earning its place."
            ),
        },
        "future_direction": (
            "Replace the fixed min_reports floor with a rate comparison against each "
            "location's own history, so a busy location is not permanently flagged "
            "simply for being busy."
        ),
    }
