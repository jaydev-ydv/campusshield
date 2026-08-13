"""Reading GPS out of a photograph, before it is destroyed.

This module runs on the bytes as received, in the same request that sanitises
them. Order matters and is not negotiable: **extract, then strip**. What is
extracted goes into the location plane (`core.report_location_detail`); the image
that reaches storage carries nothing.

## What EXIF GPS actually is

A number the camera wrote into a file. That is all. It is:

* **often absent** — WhatsApp, Signal, Instagram and most messaging apps strip it
  on send, and many phones default to off. Absence is the *normal* case and says
  nothing whatsoever about the report;
* **stale by nature** — it is where the camera was when the shutter fired, which
  may be minutes or days before the report and some distance from where the
  student is now;
* **imprecise** — consumer GPS is 5-50 m in the open and far worse indoors,
  between buildings, or on a first fix;
* **trivially forgeable** — it is a writable metadata field. Anyone with a
  command-line tool can set it to anywhere on earth.

So this is a *signal*, weighed by a human. It is never the incident location, it
never moves a pin, and it never scores anybody's credibility. The functions here
return facts and refuse to draw conclusions; :mod:`app.services.location_service`
does the comparing, and a responder does the judging.

## No forensic claim

Nothing here establishes that a photograph is authentic, unedited, or taken where
it says. It cannot. A `corroborated` result means "the number in the file is
consistent with what the student selected" — which is weak evidence of honesty
and no evidence at all of authenticity.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from PIL import Image, UnidentifiedImageError
from PIL.Image import DecompressionBombError

logger = logging.getLogger(__name__)

# EXIF tag numbers. Named here so the code below reads as intent rather than
# as magic constants.
_GPS_IFD = 0x8825
_TAG_DATETIME_ORIGINAL = 0x9003

_GPS_LAT_REF, _GPS_LAT = 1, 2
_GPS_LON_REF, _GPS_LON = 3, 4

# A rational with a zero denominator, a sexagesimal component outside its range,
# or a hemisphere reference that is not one of the four letters, is a malformed
# tag rather than a coordinate. Files like that exist in the wild — some cameras
# write 0/0 when they have no fix — and one must not read as "latitude 0".
_VALID_LAT_REF = {"N", "S"}
_VALID_LON_REF = {"E", "W"}


@dataclass(frozen=True, slots=True)
class PhotoLocation:
    """A coordinate a photograph claimed, and when it claimed to be taken.

    `captured_at` is timezone-aware only when the file said so. EXIF
    `DateTimeOriginal` carries no zone, so a naive value is left naive rather than
    being assigned one — assuming campus time would invent information.
    """

    latitude: float
    longitude: float
    captured_at: datetime | None = None


def _to_float(value) -> float | None:
    """Coerce one EXIF rational to a float, refusing the undefined ones."""
    try:
        # PIL yields IFDRational, which divides by zero silently in some versions
        # and raises in others. Reading the parts directly avoids depending on
        # which.
        numerator = getattr(value, "numerator", None)
        denominator = getattr(value, "denominator", None)
        if numerator is not None and denominator is not None:
            if denominator == 0:
                return None
            return float(numerator) / float(denominator)
        result = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _dms_to_degrees(parts) -> float | None:
    """Degrees/minutes/seconds → decimal degrees.

    Returns None for anything that is not three usable numbers. Minutes and
    seconds outside 0-60 mean the tag is malformed, not that the location is
    unusual.
    """
    try:
        if len(parts) != 3:
            return None
    except TypeError:
        return None

    degrees, minutes, seconds = (_to_float(part) for part in parts)
    if degrees is None or minutes is None or seconds is None:
        return None
    if not (0 <= minutes < 60) or not (0 <= seconds < 60):
        return None
    if degrees < 0:
        # Sign belongs to the hemisphere reference, not the degrees field.
        return None
    return degrees + minutes / 60 + seconds / 3600


def _parse_capture_time(exif) -> datetime | None:
    raw = exif.get(_TAG_DATETIME_ORIGINAL)
    if not isinstance(raw, str):
        return None
    try:
        # EXIF spells it "2026:08:10 20:14:00" — colons in the date, and no zone.
        return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        logger.info("photo carried an unparseable EXIF capture time")
        return None


def extract_photo_location(data: bytes) -> PhotoLocation | None:
    """Read GPS from image bytes, or return None.

    None is returned for every unremarkable reason — no EXIF, no GPS block, a
    malformed tag, an unreadable file — and the caller must treat all of them
    identically. A photograph without GPS is not suspicious; it is the majority.

    Never raises. A failure to parse metadata must not fail an upload: the
    sanitiser has already decided whether the file is an acceptable image, and
    this is a best-effort read of an optional extra.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            exif = image.getexif()
            if not exif:
                return None
            gps = exif.get_ifd(_GPS_IFD)
            if not gps:
                return None

            lat_ref = gps.get(_GPS_LAT_REF)
            lon_ref = gps.get(_GPS_LON_REF)
            if lat_ref not in _VALID_LAT_REF or lon_ref not in _VALID_LON_REF:
                return None

            latitude = _dms_to_degrees(gps.get(_GPS_LAT))
            longitude = _dms_to_degrees(gps.get(_GPS_LON))
            if latitude is None or longitude is None:
                return None

            if lat_ref == "S":
                latitude = -latitude
            if lon_ref == "W":
                longitude = -longitude

            # Out of range means malformed, not "somewhere unusual".
            if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
                return None

            # 0,0 is Null Island — the Gulf of Guinea. In practice it is what a
            # device writes when it has no fix, not a place anyone photographed.
            if latitude == 0 and longitude == 0:
                return None

            return PhotoLocation(
                latitude=latitude,
                longitude=longitude,
                captured_at=_parse_capture_time(exif),
            )
    except (
        UnidentifiedImageError,
        DecompressionBombError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        # Deliberately broad and deliberately silent about specifics: a
        # metadata read is optional, and a corrupt tag is not worth an error
        # anybody has to act on.
        logger.info("photo EXIF could not be read for a location signal")
        return None


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres.

    Spherical earth. At campus distances the error against a proper ellipsoid
    model is well under a metre, which is far smaller than the GPS error this is
    used to judge — a more precise formula would be precision theatre.
    """
    radius_m = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_m * math.asin(min(1.0, math.sqrt(a)))


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive timestamp so it can be stored.

    EXIF has no timezone. Storing the value at all requires choosing one, and UTC
    is chosen because it is the honest "we do not know" — pretending it was campus
    local time would silently shift every capture time by five and a half hours
    and make conflicts look like agreements.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
