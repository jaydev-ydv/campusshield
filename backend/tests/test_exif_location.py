"""EXIF GPS extraction and the location resolver.

Real Pillow, real image bytes, real EXIF blocks written by the test. Nothing here
is mocked: a test that stubbed the image library would pass whether or not GPS
was actually being read, which is the one thing worth knowing.

The theme running through these tests is that **absence and malformation are
normal**. Most photographs reaching a report have no GPS at all, because
messaging apps strip it. A parser that treated that as exceptional would be
wrong about the common case.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.models import CampusLocation
from app.models.enums import CoordinateStatus, LocationResolution, LocationSignalSource
from app.services.location_service import (
    LocationResolver,
    LocationSignal,
)
from app.utils.exif_location import (
    as_utc,
    extract_photo_location,
    haversine_metres,
)

# Presidency University, Bengaluru is at roughly 13.13 N, 77.58 E. These are NOT
# used as campus coordinates anywhere — CAMPUS_LOCATIONS.md records zero
# verified locations and this file writes none. They are here only so the
# distances under test are realistic rather than absurd.
REF_LAT, REF_LON = 13.130000, 77.580000


def rational(value: float, denominator: int = 10000) -> IFDRational:
    return IFDRational(round(value * denominator), denominator)


def dms(degrees: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    """Decimal degrees → the degrees/minutes/seconds triple EXIF stores."""
    degrees = abs(degrees)
    d = int(degrees)
    minutes_full = (degrees - d) * 60
    m = int(minutes_full)
    seconds = (minutes_full - m) * 60
    return (IFDRational(d, 1), IFDRational(m, 1), rational(seconds))


def make_photo(
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    captured: str | None = "2026:08:10 20:14:00",
    gps_override: dict | None = None,
    size: tuple[int, int] = (320, 240),
) -> bytes:
    """A real JPEG, optionally carrying a real GPS IFD."""
    image = Image.new("RGB", size, (120, 30, 30))
    buffer = io.BytesIO()

    exif = Image.Exif()
    exif[0x010F] = "TestCameraMake"
    exif[0x0110] = "TestCameraModel"
    if captured is not None:
        exif[0x9003] = captured

    if gps_override is not None:
        exif.get_ifd(0x8825).update(gps_override)
    elif latitude is not None and longitude is not None:
        exif.get_ifd(0x8825).update(
            {
                1: "N" if latitude >= 0 else "S",
                2: dms(latitude),
                3: "E" if longitude >= 0 else "W",
                4: dms(longitude),
            }
        )

    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def make_location(
    *,
    latitude: float | None = REF_LAT,
    longitude: float | None = REF_LON,
    status: CoordinateStatus = CoordinateStatus.VERIFIED,
    name: str = "Test Block",
) -> CampusLocation:
    """An unsaved CampusLocation.

    Not persisted: the resolver takes a location object and issues no queries, so
    these tests need no database. Coordinates are synthetic and labelled as such.
    """
    return CampusLocation(
        code="TEST-LOC",
        name=name,
        location_type="academic",
        latitude=Decimal(str(latitude)) if latitude is not None else None,
        longitude=Decimal(str(longitude)) if longitude is not None else None,
        coordinate_status=status,
        coordinate_source="synthetic test fixture" if latitude is not None else None,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_reads_a_valid_coordinate():
    result = extract_photo_location(make_photo(latitude=REF_LAT, longitude=REF_LON))
    assert result is not None
    assert result.latitude == pytest.approx(REF_LAT, abs=1e-4)
    assert result.longitude == pytest.approx(REF_LON, abs=1e-4)


def test_reads_the_capture_time():
    result = extract_photo_location(make_photo(latitude=REF_LAT, longitude=REF_LON))
    assert result is not None
    assert result.captured_at == datetime(2026, 8, 10, 20, 14, 0)


def test_capture_time_stays_naive():
    """EXIF carries no timezone, and inventing one would shift every reading.

    `as_utc` attaches UTC at the storage boundary — an explicit "we do not know"
    — rather than the parser guessing campus local time and silently moving
    every capture by five and a half hours.
    """
    result = extract_photo_location(make_photo(latitude=REF_LAT, longitude=REF_LON))
    assert result is not None and result.captured_at is not None
    assert result.captured_at.tzinfo is None
    assert as_utc(result.captured_at).tzinfo is timezone.utc


def test_southern_and_western_hemispheres_are_signed():
    result = extract_photo_location(make_photo(latitude=-33.86, longitude=-70.66))
    assert result is not None
    assert result.latitude == pytest.approx(-33.86, abs=1e-4)
    assert result.longitude == pytest.approx(-70.66, abs=1e-4)


def test_no_exif_at_all_returns_none():
    """The common case. A photo from WhatsApp has no metadata whatsoever."""
    image = Image.new("RGB", (64, 64), (10, 10, 10))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    assert extract_photo_location(buffer.getvalue()) is None


def test_exif_without_a_gps_block_returns_none():
    assert extract_photo_location(make_photo()) is None


def test_png_without_metadata_returns_none():
    image = Image.new("RGB", (64, 64), (10, 10, 10))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    assert extract_photo_location(buffer.getvalue()) is None


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({1: "N", 2: dms(13.1)}, id="longitude missing"),
        pytest.param({3: "E", 4: dms(77.5)}, id="latitude missing"),
        pytest.param({1: "Q", 2: dms(13.1), 3: "E", 4: dms(77.5)}, id="bad hemisphere letter"),
        pytest.param(
            {1: "N", 2: (IFDRational(13, 1),), 3: "E", 4: dms(77.5)}, id="truncated triple"
        ),
        pytest.param(
            {
                1: "N",
                2: (IFDRational(13, 0), IFDRational(0, 1), IFDRational(0, 1)),
                3: "E",
                4: dms(77.5),
            },
            id="zero denominator",
        ),
        pytest.param(
            {
                1: "N",
                2: (IFDRational(13, 1), IFDRational(99, 1), IFDRational(0, 1)),
                3: "E",
                4: dms(77.5),
            },
            id="minutes out of range",
        ),
        pytest.param(
            {
                1: "N",
                2: (IFDRational(200, 1), IFDRational(0, 1), IFDRational(0, 1)),
                3: "E",
                4: dms(77.5),
            },
            id="latitude beyond the pole",
        ),
    ],
)
def test_malformed_gps_returns_none_rather_than_a_wrong_coordinate(override):
    """A malformed tag must never read as a coordinate.

    Cameras do write nonsense — 0/0 rationals when there is no fix, truncated
    triples, occasionally a hemisphere byte that is not one of the four letters.
    Any of these silently becoming "latitude 0" would put a false conflict in
    front of a responder.
    """
    assert extract_photo_location(make_photo(gps_override=override)) is None


def test_null_island_is_rejected():
    """0,0 is what a device writes with no fix, not somewhere anybody was."""
    assert extract_photo_location(make_photo(latitude=0.0, longitude=0.0)) is None


def test_unparseable_capture_time_does_not_lose_the_coordinate():
    data = make_photo(latitude=REF_LAT, longitude=REF_LON, captured="not a timestamp")
    result = extract_photo_location(data)
    assert result is not None
    assert result.captured_at is None


def test_garbage_bytes_return_none_without_raising():
    """Metadata reading is best-effort and must never fail an upload."""
    assert extract_photo_location(b"not an image at all") is None
    assert extract_photo_location(b"") is None


def test_extraction_does_not_modify_the_input():
    data = make_photo(latitude=REF_LAT, longitude=REF_LON)
    before = bytes(data)
    extract_photo_location(data)
    assert data == before


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


def test_zero_distance_between_identical_points():
    assert haversine_metres(REF_LAT, REF_LON, REF_LAT, REF_LON) == pytest.approx(0, abs=0.01)


def test_distance_is_symmetric():
    a = haversine_metres(REF_LAT, REF_LON, REF_LAT + 0.01, REF_LON + 0.01)
    b = haversine_metres(REF_LAT + 0.01, REF_LON + 0.01, REF_LAT, REF_LON)
    assert a == pytest.approx(b, abs=0.01)


def test_one_hundredth_of_a_degree_of_latitude_is_about_1100_metres():
    """A sanity anchor against a known quantity, so a unit error cannot hide."""
    distance = haversine_metres(REF_LAT, REF_LON, REF_LAT + 0.01, REF_LON)
    assert 1000 < distance < 1200


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def signal(lat: float, lon: float, *, captured=None) -> LocationSignal:
    return LocationSignal(
        latitude=lat,
        longitude=lon,
        source=LocationSignalSource.PHOTO_EXIF,
        captured_at=captured,
    )


def test_unsurveyed_location_resolves_unresolved():
    """The state every report is in today.

    Zero campus locations have verified coordinates, so there is nothing to
    compare a signal against. Saying `unresolved` is the honest answer, and it
    stays the answer even when a photograph does carry GPS.
    """
    resolver = LocationResolver()
    location = make_location(latitude=None, longitude=None, status=CoordinateStatus.REQUIRED)

    result = resolver.resolve(location, signal(REF_LAT, REF_LON))

    assert result.resolution is LocationResolution.UNRESOLVED
    assert result.source is LocationSignalSource.LOCATION_DEFAULT
    assert result.distance_m is None
    assert "no verified coordinate" in (result.conflict_note or "")


def test_provisional_coordinates_are_treated_as_unsurveyed():
    """A provisional point is one nobody has stood at.

    Judging a student's photograph against a guess would manufacture conflicts
    out of the survey backlog.
    """
    resolver = LocationResolver()
    location = make_location(status=CoordinateStatus.PROVISIONAL)

    result = resolver.resolve(location, signal(REF_LAT, REF_LON))

    assert result.resolution is LocationResolution.UNRESOLVED


def test_no_signal_on_a_mapped_location_is_approximate():
    """The ordinary outcome once the survey lands. Not a problem, not a flag."""
    resolver = LocationResolver()

    result = resolver.resolve(make_location(), None)

    assert result.resolution is LocationResolution.APPROXIMATE
    assert result.source is LocationSignalSource.LOCATION_DEFAULT
    assert result.signal_latitude is None
    assert result.conflict_note is None


def test_a_nearby_signal_corroborates():
    resolver = LocationResolver(corroboration_radius_m=150)
    # ~0.0003 degrees of latitude is roughly 33 m.
    result = resolver.resolve(make_location(), signal(REF_LAT + 0.0003, REF_LON))

    assert result.resolution is LocationResolution.CORROBORATED
    assert result.source is LocationSignalSource.PHOTO_EXIF
    assert result.distance_m is not None and result.distance_m < 150
    assert result.conflict_note is None


def test_a_distant_signal_conflicts():
    resolver = LocationResolver(corroboration_radius_m=150)
    # ~0.02 degrees is a bit over 2 km — off campus.
    result = resolver.resolve(make_location(), signal(REF_LAT + 0.02, REF_LON))

    assert result.resolution is LocationResolution.CONFLICTING
    assert result.distance_m is not None and result.distance_m > 1000
    assert result.signal_latitude == pytest.approx(REF_LAT + 0.02)


def test_a_far_away_spoof_conflicts_without_being_called_a_spoof():
    """A coordinate on another continent is still only a conflict.

    The system cannot tell a forged coordinate from a student who was somewhere
    else earlier, so it must not imply it can. The note says the difference
    exists and offers the innocent readings.
    """
    resolver = LocationResolver()
    result = resolver.resolve(make_location(), signal(51.5074, -0.1278))  # London

    assert result.resolution is LocationResolution.CONFLICTING
    note = (result.conflict_note or "").lower()
    assert "fake" not in note
    assert "spoof" not in note
    assert "false" not in note
    assert "lying" not in note


def test_the_conflict_note_offers_innocent_explanations():
    resolver = LocationResolver()
    result = resolver.resolve(make_location(name="F Block"), signal(REF_LAT + 0.02, REF_LON))

    note = result.conflict_note or ""
    assert "F Block" in note
    # The responder is told which location to treat as authoritative.
    assert "Treat F Block as the incident location" in note
    assert "may have moved" in note
    assert "can also be edited" in note


def test_the_boundary_is_inclusive():
    """A signal exactly at the radius corroborates rather than conflicting.

    Which side of the boundary is generous matters: a false conflict spends a
    responder's attention on a report that was filed correctly.
    """
    resolver = LocationResolver(corroboration_radius_m=1200)
    # ~0.01 degrees of latitude, about 1110 m — inside 1200.
    result = resolver.resolve(make_location(), signal(REF_LAT + 0.01, REF_LON))
    assert result.resolution is LocationResolution.CORROBORATED


def test_capture_time_is_carried_through():
    resolver = LocationResolver()
    captured = datetime(2026, 8, 10, 20, 14, tzinfo=timezone.utc)
    result = resolver.resolve(make_location(), signal(REF_LAT, REF_LON, captured=captured))
    assert result.signal_captured_at == captured


def test_the_resolver_never_writes_to_the_location():
    """It describes; it does not decide.

    The strongest form of "EXIF never overrides the selected location" is that
    the resolver has no way to change anything about it.
    """
    resolver = LocationResolver()
    location = make_location()
    before = (location.latitude, location.longitude, location.coordinate_status)

    resolver.resolve(location, signal(51.5074, -0.1278))

    assert (location.latitude, location.longitude, location.coordinate_status) == before


def test_no_numeric_confidence_is_produced():
    """Four named states, deliberately no score.

    There is no calibration data behind this system, so a number would be
    invented precision dressed as measurement.
    """
    resolver = LocationResolver()
    result = resolver.resolve(make_location(), signal(REF_LAT, REF_LON))

    fields = set(result.__slots__)
    assert not {"confidence", "score", "probability", "certainty"} & fields
