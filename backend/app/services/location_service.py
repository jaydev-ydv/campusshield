"""Resolving a corroborating location signal against the selected campus location.

## The rule this module exists to enforce

**The student's selected `location_id` is the operational truth.** Nothing here
changes it, and nothing here can. `resolve()` returns a *description* of how a
signal compared with that selection; the report's location is set at submission
from `location_id` and is never revisited.

That is a deliberate architectural constraint, not a limitation waiting to be
lifted. `core.report.location_id` is a foreign key that hotspot detection,
cluster centroids, `v_public_safety_map` and every before/after impact
measurement depend on. A coordinate that silently overrode it would fragment all
of them — and would do so on the strength of a number that a student can set to
anything.

## The four states

| State | Means |
|---|---|
| `corroborated` | A signal exists, the location is surveyed, and they agree
  within the policy radius. |
| `approximate` | The location is surveyed; nothing corroborates the precise
  point. **The ordinary case.** |
| `conflicting` | A signal exists, the location is surveyed, and they disagree.
  Shown to a human. |
| `unresolved` | Not enough information — most often because the location
  has no verified coordinate. |

**Every report resolves to `unresolved` today**, because `core.campus_location`
holds zero verified coordinates. That is the honest answer, not a stub, and it
will change when the survey lands and not before.

## Why a conflict is never resolved automatically

A student who fled the scene and reported from their room half an hour later
produces exactly the same reading as a forged coordinate: a photo whose GPS is
some distance from the selected location. There is no signal available that
separates them. So the system states both facts and lets a responder judge.
Picking either interpretation would be guessing while looking authoritative,
and the wrong guess in the first direction disbelieves someone who was telling
the truth about being attacked.

No numeric confidence score is produced. There is no calibration dataset behind
this system, so a number would be invented precision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..models import CampusLocation
from ..models.enums import CoordinateStatus, LocationResolution, LocationSignalSource
from ..utils.exif_location import haversine_metres

logger = logging.getLogger(__name__)

# Used only if core.system_policy has no row, which the migration guarantees it
# does. Present so the resolver is constructible in a unit test without a
# database.
DEFAULT_CORROBORATION_RADIUS_M = 150


@dataclass(frozen=True, slots=True)
class LocationSignal:
    """A coordinate something claimed, and where it came from."""

    latitude: float
    longitude: float
    source: LocationSignalSource
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    """The resolver's verdict. Descriptive only — nothing here moves a pin."""

    resolution: LocationResolution
    source: LocationSignalSource
    signal_latitude: float | None = None
    signal_longitude: float | None = None
    distance_m: int | None = None
    signal_captured_at: datetime | None = None
    conflict_note: str | None = None


# How far a device position may be from a verified location and still count as
# "this is that place" for the emergency path. Wider than
# DEFAULT_CORROBORATION_RADIUS_M deliberately: corroboration is confirming a
# place the student already chose, this is searching campus-wide for the one
# place a raw coordinate is nearest to, with no prior selection to anchor it.
DEFAULT_EMERGENCY_MATCH_RADIUS_M = 300


def nearest_verified_location(
    latitude: float,
    longitude: float,
    candidates: list[CampusLocation],
    *,
    max_distance_m: int = DEFAULT_EMERGENCY_MATCH_RADIUS_M,
) -> CampusLocation | None:
    """The closest verified location to a raw coordinate, if any is close enough.

    Built for the emergency ("SOS") path: a browser position arrives with no
    location the student picked to compare it against, so there is nothing for
    :meth:`LocationResolver.resolve` to corroborate. This answers a different
    question — *which* surveyed place, if any, is this near? — so the emergency
    report can be anchored to a real, named location instead of the
    "unspecified" sentinel whenever that's honestly possible.

    ``candidates`` should come from :meth:`LocationRepository.list_active`,
    which already guarantees every row is ``verified`` and has coordinates —
    this function does not re-check that, and will raise if handed a row
    without one.

    Returns ``None`` rather than guessing when nothing is within
    ``max_distance_m``. Today this always returns ``None``: zero campus
    locations are verified yet, so there is nothing to match against. That is
    the honest state of the survey, not a bug in this function.
    """
    best: CampusLocation | None = None
    best_distance = float("inf")
    for candidate in candidates:
        if candidate.latitude is None or candidate.longitude is None:
            continue
        distance = haversine_metres(
            latitude, longitude, float(candidate.latitude), float(candidate.longitude)
        )
        if distance <= max_distance_m and distance < best_distance:
            best, best_distance = candidate, distance
    return best


class LocationResolver:
    def __init__(self, *, corroboration_radius_m: int = DEFAULT_CORROBORATION_RADIUS_M) -> None:
        self._radius_m = corroboration_radius_m

    def resolve(self, location: CampusLocation, signal: LocationSignal | None) -> ResolvedLocation:
        """Compare a signal against the selected campus location.

        `location` is the location the student chose. It is read, never written.
        """
        surveyed = self._surveyed_point(location)

        if surveyed is None:
            # The location has no verified coordinate, so there is nothing to
            # compare against. This is where every report lands until the campus
            # survey is done, and it is not a failure — the report is complete
            # and correctly anchored; the map simply cannot draw it yet.
            return ResolvedLocation(
                resolution=LocationResolution.UNRESOLVED,
                source=LocationSignalSource.LOCATION_DEFAULT,
                conflict_note=(
                    "This campus location has no verified coordinate yet, so no "
                    "position check could be made."
                ),
            )

        if signal is None:
            # No photo GPS, no device position. Overwhelmingly the common case,
            # and entirely unremarkable: most images arrive with metadata already
            # stripped by whatever app they passed through.
            return ResolvedLocation(
                resolution=LocationResolution.APPROXIMATE,
                source=LocationSignalSource.LOCATION_DEFAULT,
            )

        location_lat, location_lon = surveyed
        distance = haversine_metres(signal.latitude, signal.longitude, location_lat, location_lon)
        distance_m = round(distance)

        if distance <= self._radius_m:
            return ResolvedLocation(
                resolution=LocationResolution.CORROBORATED,
                source=signal.source,
                signal_latitude=signal.latitude,
                signal_longitude=signal.longitude,
                distance_m=distance_m,
                signal_captured_at=signal.captured_at,
                conflict_note=None,
            )

        return ResolvedLocation(
            resolution=LocationResolution.CONFLICTING,
            source=signal.source,
            signal_latitude=signal.latitude,
            signal_longitude=signal.longitude,
            distance_m=distance_m,
            signal_captured_at=signal.captured_at,
            conflict_note=self._conflict_note(location, signal, distance_m),
        )

    @staticmethod
    def _surveyed_point(location: CampusLocation) -> tuple[float, float] | None:
        """The location's own coordinate, only if it is actually verified.

        `provisional` is treated as absent. A provisional coordinate is one
        nobody has stood at, and judging a student's photograph against a guess
        would manufacture conflicts out of the survey backlog.
        """
        if location.coordinate_status is not CoordinateStatus.VERIFIED:
            return None
        if location.latitude is None or location.longitude is None:
            return None
        return float(location.latitude), float(location.longitude)

    def _conflict_note(
        self, location: CampusLocation, signal: LocationSignal, distance_m: int
    ) -> str:
        """Prose a responder can act on.

        States what was selected, what the signal said, and — importantly — the
        innocent explanations, so the note does not read as an accusation. It
        carries no raw EXIF: a distance and a source, nothing more.
        """
        origin = {
            LocationSignalSource.PHOTO_EXIF: "The attached photograph's location data",
            LocationSignalSource.DEVICE_GPS: "The reporting device's position",
            LocationSignalSource.LOCATION_DEFAULT: "The available position data",
        }[signal.source]

        return (
            f"{origin} is about {distance_m} m from {location.name}, which the "
            f"reporter selected. Treat {location.name} as the incident location. "
            "A difference like this is common and usually innocent: the reporter "
            "may have moved before reporting, the photograph may have been taken "
            "elsewhere or earlier, or the device's position may simply be "
            "inaccurate. Photo location data can also be edited, so it does not "
            "establish where a picture was taken."
        )
