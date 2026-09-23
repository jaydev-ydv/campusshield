"""Demo campus locations — synthetic, clearly labelled, never real (Phase 5B).

    python backend/scripts/seed_demo_campus_locations.py

Makes the map experience actually visible and usable in a development or
demo environment, without claiming a single real Presidency University
coordinate. Every row this script writes carries `is_synthetic = true` and
a `coordinate_source` beginning with the literal string `'DEMO FIXTURE:'` —
the database's own CHECK constraint
(`ck_campus_location_synthetic_is_labelled`, migration `0005`) refuses any
row that sets `is_synthetic = true` without that prefix, so this script's
discipline is backed by the schema, not merely by this file behaving.

## Where the pins are, and why

A small cluster near `(0, 0)` — Null Island, the Gulf of Guinea — offset by
a few thousandths of a degree so five locations render as distinct,
sensibly-spaced pins on a map rather than one dot. This is deliberately
**not** near Bengaluru or Presidency University: a demo pin that happened to
land close to the real campus would risk exactly the confusion this phase
exists to prevent. Anyone looking at a world map showing these pins sees
immediately that they are in the ocean, not on a campus — geography doing
the same job the `is_synthetic` flag and the "DEMO —" name prefix already
do at the data level.

## What this script is not

Not `scripts/import_campus_locations.py` (that one loads real survey data
and has no `is_synthetic` column in its CSV format at all — it cannot
produce a demo row if it tried). Not `scripts/seed_dev_data.py` (accounts
only, and its own docstring explains at length why it refuses to create
any campus location, real or synthetic). This script has exactly one job:
a demo/development campus, and it refuses to run anywhere its output could
be mistaken for a real deployment's seed data.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

DEMO_SOURCE = "DEMO FIXTURE: synthetic development data, not a real field survey"

ZONE = {"code": "DEMO-ZONE", "name": "Demo Zone (NOT A REAL CAMPUS ZONE)"}

# (code, name, type, lat_offset, lon_offset, is_indoor, dispatch_note, footfall_band)
LOCATIONS = [
    (
        "DEMO-LIBRARY",
        "DEMO — Sample Library (NOT A REAL LOCATION)",
        "library",
        0.0010,
        0.0010,
        True,
        "Demo access note: enter via the main doors.",
        "high",
    ),
    (
        "DEMO-HOSTEL",
        "DEMO — Sample Hostel (NOT A REAL LOCATION)",
        "hostel",
        0.0025,
        0.0015,
        True,
        "Demo access note: reception desk is at the ground floor entrance.",
        "medium",
    ),
    (
        "DEMO-CAFETERIA",
        "DEMO — Sample Cafeteria (NOT A REAL LOCATION)",
        "dining",
        0.0015,
        0.0030,
        True,
        "Demo access note: adjacent to the main walkway.",
        "high",
    ),
    (
        "DEMO-GATE",
        "DEMO — Sample Main Gate (NOT A REAL LOCATION)",
        "gate",
        -0.0005,
        0.0005,
        False,
        "Demo access note: security post is immediately inside the gate.",
        "high",
    ),
    (
        "DEMO-SPORTS",
        "DEMO — Sample Sports Ground (NOT A REAL LOCATION)",
        "sports",
        0.0030,
        -0.0010,
        False,
        "Demo access note: open ground, no single entrance.",
        "medium",
    ),
]


def resolve_database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        env = pathlib.Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if url:
        from app.config import _normalise_db_url
        url = _normalise_db_url(url)
    return url or None


def main() -> int:
    url = resolve_database_url()
    if not url:
        print("DATABASE_URL is not set")
        return 2

    name = url.rsplit("/", 1)[-1].split("?")[0]
    if any(token in name.lower() for token in ("prod", "live")):
        print(f"refusing to seed demo campus data into {name!r}")
        return 1

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO core.campus_zone (code, name) "
                "VALUES (:code, :name) ON CONFLICT (code) DO NOTHING"
            ),
            ZONE,
        )
        zone_id = conn.execute(
            text("SELECT zone_id FROM core.campus_zone WHERE code = :code"),
            {"code": ZONE["code"]},
        ).scalar_one()

        inserted = 0
        skipped = 0
        for code, loc_name, loc_type, lat, lon, is_indoor, note, footfall in LOCATIONS:
            existing = conn.execute(
                text("SELECT 1 FROM core.campus_location WHERE code = :code"), {"code": code}
            ).first()
            if existing:
                skipped += 1
                continue
            conn.execute(
                text(
                    "INSERT INTO core.campus_location "
                    "(code, name, location_type, zone_id, latitude, longitude, "
                    " coordinate_status, coordinate_source, coordinate_captured_at, "
                    " is_indoor, footfall_band, dispatch_note, is_active, is_synthetic) "
                    "VALUES "
                    "(:code, :name, :location_type, :zone_id, :latitude, :longitude, "
                    " 'verified', :source, now(), :is_indoor, :footfall_band, "
                    " :dispatch_note, true, true)"
                ),
                {
                    "code": code,
                    "name": loc_name,
                    "location_type": loc_type,
                    "zone_id": zone_id,
                    "latitude": lat,
                    "longitude": lon,
                    "source": DEMO_SOURCE,
                    "is_indoor": is_indoor,
                    "footfall_band": footfall,
                    "dispatch_note": note,
                },
            )
            inserted += 1

    print(f"Demo campus locations: {inserted} inserted, {skipped} already present (skipped).")
    print("Every row is is_synthetic = true and will never appear as verified production data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
