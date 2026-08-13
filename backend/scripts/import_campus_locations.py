"""Real campus-data import — validated, repeatable, insert-only.

    python backend/scripts/import_campus_locations.py \\
        --locations path/to/locations.csv \\
        [--zones path/to/zones.csv] \\
        [--commit]

Loads rows into ``core.campus_zone`` and ``core.campus_location`` from CSV.
This is the mechanism CAMPUS_LOCATIONS.md asks for and DATABASE_SETUP.md §8
previously left as one-row-at-a-time SQL: a repeatable way to load an actual
field survey once one exists.

## What this script will not do

It will not invent a coordinate, a zone, or a name. Every value in the
database after a run is a value that was in the CSV — nothing is defaulted
except ``coordinate_status`` (→ ``required``) and ``is_active`` (→
``false``), which mirror the column defaults already declared in migration
``0001``. If the real survey has not happened, the correct CSV content is
rows with a code, a name, and nothing else — see the ``templates/`` example.

## Validation happens before any write

The whole file is parsed and validated first. If any row fails, **nothing is
written** — not the valid rows before it, not the zones. A partially-applied
import would leave the vocabulary in a state nobody chose. Every rule below
mirrors a CHECK constraint already enforced by the schema (migration 0001);
this script exists so a mistake is a clear message with a row number, not an
opaque ``IntegrityError`` after half the file has already been considered.

Rules enforced:

- ``code`` and ``name`` are required and non-blank.
- ``code`` is unique **within the file**. A code already present in the
  database is not an error — it is skipped and reported, which is what makes
  re-running the same file safe (see "Repeatable" below).
- ``location_type`` and ``footfall_band``, if given, are one of the values
  the database's own CHECK constraints allow.
- ``zone_code``, if given, must resolve to a zone — either already in the
  database or present in ``--zones`` — or the row is rejected. No location
  is silently left in an unzoned state by a typo.
- ``latitude``/``longitude`` are both present or both absent, and each is
  numerically in range. Both required together mirrors
  ``ck_campus_location_coords_paired``.
- ``coordinate_status = required`` forbids coordinates on the same row
  (``ck_campus_location_required_has_no_coords``).
- ``coordinate_status = verified`` requires latitude, longitude,
  ``coordinate_source``, and ``coordinate_captured_at`` all present
  (``ck_campus_location_verified_is_sourced``). ``coordinate_captured_at``
  must carry a UTC offset — a naive timestamp does not say what timezone the
  surveyor stood in, and guessing one is exactly the kind of invented
  precision this project refuses elsewhere (see ``exif_location.py``).
- ``is_active = true`` requires ``coordinate_status = verified``
  (``ck_campus_location_active_requires_verified``). This script will not
  set a location active on anything less than a verified, sourced
  coordinate — there is no flag to override it.
- Two rows in the same file with the identical (rounded to 6 decimal
  places) coordinate are rejected as an ambiguous duplicate — in practice
  always a copy-paste of one survey point onto two rows, not two distinct
  places at the same point on Earth.
- Two rows in the same file with the same name (case- and
  whitespace-insensitive) are rejected. CAMPUS_LOCATIONS.md names this
  exact failure mode itself (`CAFE-MAIN` vs `CANTEEN-MAIN`, possibly the
  same place under two aggregator names) — the file must resolve it before
  import, not after.

## Repeatable, not idempotent-by-magic

A code already in the database is skipped, not re-validated against the
file and not updated. **This script never updates an existing row.**
Promoting a staged (`required`) location to `verified` — the actual
end-of-survey action — stays the deliberate, one-row SQL `UPDATE` in
DATABASE_SETUP.md §8. That is not this script's laziness: promotion is a
"someone stood here and confirmed it" act performed once per place, and a
bulk tool is the wrong shape for it. Bulk loading is for getting the
un-surveyed vocabulary (or a batch of freshly-surveyed rows) into the table
in the first place.

## Dry run by default

Without ``--commit``, this script validates and reports what it *would* do
and writes nothing. Passing ``--commit`` is the one flag that makes this
script touch a database — read it before you type it.

## No refusal of a production-looking database name

Unlike ``seed_dev_data.py``, this script does not refuse a database whose
name suggests production. That script exists to keep *synthetic* demo data
out of anywhere real; this one exists to put *real, validated* survey data
into wherever the operator points it, production included. Pointing it at
the wrong database is the operator's responsibility, the same as any other
migration-adjacent tool — `--dry-run` (the default) is the safeguard.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import pathlib
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

ALLOWED_LOCATION_TYPES = {
    "academic",
    "library",
    "laboratory",
    "hostel",
    "dining",
    "sports",
    "sports_support",
    "assembly",
    "open_assembly",
    "recreation",
    "health",
    "support",
    "amenity",
    "admin",
    "gate",
    "parking",
    "transit",
    "circulation",
    "path",
    "open_ground",
    "other",
}
ALLOWED_COORDINATE_STATUS = {"required", "provisional", "verified"}
ALLOWED_FOOTFALL_BANDS = {"high", "medium", "low"}
ALLOWED_ROLES = {"student", "security", "icc", "admin"}

LOCATION_COLUMNS = [
    "code",
    "name",
    "location_type",
    "zone_code",
    "latitude",
    "longitude",
    "coordinate_status",
    "coordinate_source",
    "coordinate_captured_at",
    "is_indoor",
    "has_lighting",
    "has_cctv",
    "footfall_band",
    "dispatch_note",
    "is_active",
]
ZONE_COLUMNS = ["code", "name", "description", "responsible_role"]


class ImportValidationError(Exception):
    """One or more rows failed validation. Nothing was written."""


@dataclasses.dataclass(frozen=True, slots=True)
class ZoneRow:
    code: str
    name: str
    description: str | None
    responsible_role: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class LocationRow:
    code: str
    name: str
    location_type: str | None
    zone_code: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    coordinate_status: str
    coordinate_source: str | None
    coordinate_captured_at: datetime | None
    is_indoor: bool | None
    has_lighting: bool | None
    has_cctv: bool | None
    footfall_band: str | None
    dispatch_note: str | None
    is_active: bool


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_bool(value: str | None, *, field: str, row_num: int) -> bool | None:
    raw = _blank(value)
    if raw is None:
        return None
    lowered = raw.lower()
    if lowered in ("true", "t", "yes", "1"):
        return True
    if lowered in ("false", "f", "no", "0"):
        return False
    raise ImportValidationError(f"row {row_num}: {field} must be true/false, got {raw!r}")


def _parse_decimal(value: str | None, *, field: str, row_num: int) -> Decimal | None:
    raw = _blank(value)
    if raw is None:
        return None
    try:
        return Decimal(raw).quantize(Decimal("0.000001"))
    except InvalidOperation:
        raise ImportValidationError(
            f"row {row_num}: {field} is not a valid decimal number: {raw!r}"
        ) from None


def _parse_datetime(value: str | None, *, field: str, row_num: int) -> datetime | None:
    raw = _blank(value)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ImportValidationError(
            f"row {row_num}: {field} is not a valid ISO 8601 timestamp: {raw!r}"
        ) from None
    if parsed.tzinfo is None:
        raise ImportValidationError(
            f"row {row_num}: {field} has no UTC offset ({raw!r}). "
            "State one explicitly (e.g. '+05:30') — a naive timestamp does not "
            "say what timezone the surveyor stood in, and this script will not "
            "guess one."
        )
    return parsed


def parse_zones(path: pathlib.Path) -> list[ZoneRow]:
    rows: list[ZoneRow] = []
    seen_codes: set[str] = set()

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(ZONE_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ImportValidationError(
                f"{path}: missing required column(s): {', '.join(sorted(missing))}"
            )

        for row_num, raw in enumerate(reader, start=2):
            code = _blank(raw["code"])
            name = _blank(raw["name"])
            if code is None:
                raise ImportValidationError(f"row {row_num}: zone code is required")
            if name is None:
                raise ImportValidationError(f"row {row_num}: zone name is required")
            if code in seen_codes:
                raise ImportValidationError(
                    f"row {row_num}: duplicate zone code {code!r} within this file"
                )
            seen_codes.add(code)

            role = _blank(raw.get("responsible_role"))
            if role is not None and role not in ALLOWED_ROLES:
                raise ImportValidationError(
                    f"row {row_num}: responsible_role {role!r} is not one of "
                    f"{sorted(ALLOWED_ROLES)}"
                )
            if role == "student":
                raise ImportValidationError(
                    f"row {row_num}: responsible_role cannot be 'student' (ck_campus_zone_role)"
                )

            rows.append(
                ZoneRow(
                    code=code,
                    name=name,
                    description=_blank(raw.get("description")),
                    responsible_role=role,
                )
            )

    return rows


def parse_locations(path: pathlib.Path) -> list[LocationRow]:
    rows: list[LocationRow] = []
    seen_codes: set[str] = set()
    seen_names: dict[str, int] = {}
    seen_coords: dict[tuple[Decimal, Decimal], int] = {}

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(LOCATION_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ImportValidationError(
                f"{path}: missing required column(s): {', '.join(sorted(missing))}"
            )

        for row_num, raw in enumerate(reader, start=2):
            code = _blank(raw["code"])
            name = _blank(raw["name"])
            if code is None:
                raise ImportValidationError(f"row {row_num}: code is required")
            if name is None:
                raise ImportValidationError(f"row {row_num}: name is required")
            if code in seen_codes:
                raise ImportValidationError(
                    f"row {row_num}: duplicate code {code!r} within this file"
                )
            seen_codes.add(code)

            normalised_name = " ".join(name.lower().split())
            if normalised_name in seen_names:
                raise ImportValidationError(
                    f"row {row_num}: name {name!r} duplicates row "
                    f"{seen_names[normalised_name]} ({code!r}). If these are "
                    "genuinely different places, give them distinguishing names "
                    "before import — CAMPUS_LOCATIONS.md §1 records exactly this "
                    "kind of ambiguity (e.g. CAFE-MAIN vs CANTEEN-MAIN)."
                )
            seen_names[normalised_name] = row_num

            location_type = _blank(raw.get("location_type"))
            if location_type is not None and location_type not in ALLOWED_LOCATION_TYPES:
                raise ImportValidationError(
                    f"row {row_num}: location_type {location_type!r} is not one of "
                    f"{sorted(ALLOWED_LOCATION_TYPES)}"
                )

            footfall_band = _blank(raw.get("footfall_band"))
            if footfall_band is not None and footfall_band not in ALLOWED_FOOTFALL_BANDS:
                raise ImportValidationError(
                    f"row {row_num}: footfall_band {footfall_band!r} is not one of "
                    f"{sorted(ALLOWED_FOOTFALL_BANDS)}"
                )

            latitude = _parse_decimal(raw.get("latitude"), field="latitude", row_num=row_num)
            longitude = _parse_decimal(raw.get("longitude"), field="longitude", row_num=row_num)
            if (latitude is None) != (longitude is None):
                raise ImportValidationError(
                    f"row {row_num}: latitude and longitude must both be present or "
                    "both absent (ck_campus_location_coords_paired)"
                )
            if latitude is not None and not (Decimal(-90) <= latitude <= Decimal(90)):
                raise ImportValidationError(
                    f"row {row_num}: latitude {latitude} is out of range (-90..90)"
                )
            if longitude is not None and not (Decimal(-180) <= longitude <= Decimal(180)):
                raise ImportValidationError(
                    f"row {row_num}: longitude {longitude} is out of range (-180..180)"
                )
            if latitude is not None and longitude is not None:
                key = (latitude, longitude)
                if key in seen_coords:
                    raise ImportValidationError(
                        f"row {row_num}: coordinate ({latitude}, {longitude}) duplicates "
                        f"row {seen_coords[key]} exactly. Two distinct places do not "
                        "share a GPS point to six decimal places — this is almost "
                        "always a copy-paste of one survey reading onto two rows."
                    )
                seen_coords[key] = row_num

            coordinate_status = _blank(raw.get("coordinate_status")) or "required"
            if coordinate_status not in ALLOWED_COORDINATE_STATUS:
                raise ImportValidationError(
                    f"row {row_num}: coordinate_status {coordinate_status!r} is not one "
                    f"of {sorted(ALLOWED_COORDINATE_STATUS)}"
                )

            coordinate_source = _blank(raw.get("coordinate_source"))
            coordinate_captured_at = _parse_datetime(
                raw.get("coordinate_captured_at"),
                field="coordinate_captured_at",
                row_num=row_num,
            )

            if coordinate_status == "required" and latitude is not None:
                raise ImportValidationError(
                    f"row {row_num}: coordinate_status is 'required' but latitude/"
                    "longitude are set (ck_campus_location_required_has_no_coords). "
                    "A 'required' row is a survey placeholder — it must carry no "
                    "coordinate at all."
                )
            if coordinate_status == "verified" and not (
                latitude is not None
                and coordinate_source is not None
                and coordinate_captured_at is not None
            ):
                raise ImportValidationError(
                    f"row {row_num}: coordinate_status is 'verified', which requires "
                    "latitude, longitude, coordinate_source, and "
                    "coordinate_captured_at all present "
                    "(ck_campus_location_verified_is_sourced)"
                )

            is_active = _parse_bool(raw.get("is_active"), field="is_active", row_num=row_num)
            if is_active is None:
                is_active = False
            if is_active and coordinate_status != "verified":
                raise ImportValidationError(
                    f"row {row_num}: is_active is true but coordinate_status is "
                    f"{coordinate_status!r}, not 'verified' "
                    "(ck_campus_location_active_requires_verified)"
                )

            rows.append(
                LocationRow(
                    code=code,
                    name=name,
                    location_type=location_type,
                    zone_code=_blank(raw.get("zone_code")),
                    latitude=latitude,
                    longitude=longitude,
                    coordinate_status=coordinate_status,
                    coordinate_source=coordinate_source,
                    coordinate_captured_at=coordinate_captured_at,
                    is_indoor=_parse_bool(raw.get("is_indoor"), field="is_indoor", row_num=row_num),
                    has_lighting=_parse_bool(
                        raw.get("has_lighting"), field="has_lighting", row_num=row_num
                    ),
                    has_cctv=_parse_bool(raw.get("has_cctv"), field="has_cctv", row_num=row_num),
                    footfall_band=footfall_band,
                    dispatch_note=_blank(raw.get("dispatch_note")),
                    is_active=is_active,
                )
            )

    return rows


def resolve_database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if not url:
        env = pathlib.Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
                    break
    if not url:
        raise SystemExit("DATABASE_URL is not set and --database-url was not given")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run(
    *,
    locations_path: pathlib.Path,
    zones_path: pathlib.Path | None,
    database_url: str,
    commit: bool,
) -> int:
    zones = parse_zones(zones_path) if zones_path else []
    locations = parse_locations(locations_path)

    zone_codes_referenced = {row.zone_code for row in locations if row.zone_code}
    zone_codes_in_file = {row.code for row in zones}

    engine = create_engine(database_url, future=True)
    with engine.connect() as conn:
        existing_zone_codes = set(
            conn.execute(text("SELECT code FROM core.campus_zone")).scalars().all()
        )
        existing_location_codes = set(
            conn.execute(text("SELECT code FROM core.campus_location")).scalars().all()
        )

    unresolved_zones = zone_codes_referenced - zone_codes_in_file - existing_zone_codes
    if unresolved_zones:
        raise ImportValidationError(
            "location(s) reference zone_code(s) not found in --zones or the "
            f"database: {sorted(unresolved_zones)}"
        )

    new_zones = [z for z in zones if z.code not in existing_zone_codes]
    skipped_zones = [z for z in zones if z.code in existing_zone_codes]
    new_locations = [loc for loc in locations if loc.code not in existing_location_codes]
    skipped_locations = [loc for loc in locations if loc.code in existing_location_codes]

    print(f"Zones:     {len(new_zones)} to insert, {len(skipped_zones)} already present (skipped)")
    print(
        f"Locations: {len(new_locations)} to insert, "
        f"{len(skipped_locations)} already present (skipped)"
    )
    by_status: dict[str, int] = {}
    for loc in new_locations:
        by_status[loc.coordinate_status] = by_status.get(loc.coordinate_status, 0) + 1
    for status, count in sorted(by_status.items()):
        print(f"           {count} at coordinate_status={status}")

    if skipped_zones:
        print("Skipped zones (already in database):", ", ".join(z.code for z in skipped_zones))
    if skipped_locations:
        print(
            "Skipped locations (already in database):",
            ", ".join(loc.code for loc in skipped_locations),
        )

    if not commit:
        print("\nDry run — nothing written. Pass --commit to apply.")
        return 0

    if not new_zones and not new_locations:
        print("\nNothing new to insert.")
        return 0

    with engine.begin() as conn:
        for zone in new_zones:
            conn.execute(
                text(
                    "INSERT INTO core.campus_zone (code, name, description, responsible_role) "
                    "VALUES (:code, :name, :description, "
                    "CAST(:responsible_role AS public.user_role))"
                ),
                dataclasses.asdict(zone),
            )

        zone_id_by_code: dict[str, int] = {}
        if new_locations:
            all_zone_rows = conn.execute(text("SELECT code, zone_id FROM core.campus_zone")).all()
            zone_id_by_code = {code: zone_id for code, zone_id in all_zone_rows}  # noqa: C416

        for loc in new_locations:
            conn.execute(
                text(
                    "INSERT INTO core.campus_location "
                    "(code, name, location_type, zone_id, latitude, longitude, "
                    " coordinate_status, coordinate_source, coordinate_captured_at, "
                    " is_indoor, has_lighting, has_cctv, footfall_band, dispatch_note, "
                    " is_active) "
                    "VALUES "
                    "(:code, :name, :location_type, :zone_id, :latitude, :longitude, "
                    " CAST(:coordinate_status AS public.coordinate_status), "
                    " :coordinate_source, :coordinate_captured_at, "
                    " :is_indoor, :has_lighting, :has_cctv, :footfall_band, "
                    " :dispatch_note, :is_active)"
                ),
                {
                    "code": loc.code,
                    "name": loc.name,
                    "location_type": loc.location_type,
                    "zone_id": zone_id_by_code.get(loc.zone_code) if loc.zone_code else None,
                    "latitude": loc.latitude,
                    "longitude": loc.longitude,
                    "coordinate_status": loc.coordinate_status,
                    "coordinate_source": loc.coordinate_source,
                    "coordinate_captured_at": loc.coordinate_captured_at,
                    "is_indoor": loc.is_indoor,
                    "has_lighting": loc.has_lighting,
                    "has_cctv": loc.has_cctv,
                    "footfall_band": loc.footfall_band,
                    "dispatch_note": loc.dispatch_note,
                    "is_active": loc.is_active,
                },
            )

    print(f"\nCommitted: {len(new_zones)} zone(s), {len(new_locations)} location(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--locations", required=True, type=pathlib.Path)
    parser.add_argument("--zones", type=pathlib.Path, default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write to the database. Without this flag, validate and report only.",
    )
    args = parser.parse_args()

    try:
        database_url = resolve_database_url(args.database_url)
        return run(
            locations_path=args.locations,
            zones_path=args.zones,
            database_url=database_url,
            commit=args.commit,
        )
    except ImportValidationError as exc:
        print(f"\nImport rejected: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
