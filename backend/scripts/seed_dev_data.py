"""Development-only demo data.

    python backend/scripts/seed_dev_data.py

Creates one account per role so the API can be exercised with the development
authentication provider.

**This script does not create campus locations, and must not.**
``core.campus_location`` is the controlled vocabulary every report anchors to,
and every downstream feature — the safety map, hotspot detection, cluster
centroids, before/after impact measurement — reads its coordinates. A location
with invented coordinates does not fail; it produces a map pin in the wrong
place, a hotspot at a location nobody visited, and an impact measurement against
a baseline that never existed. Wrong data that looks right is worse than no data,
because no data is visibly missing.

CAMPUS_LOCATIONS.md records **zero verified coordinates** for Presidency
University. Until the field survey happens, the correct contents of
``core.campus_location`` is nothing at all.

To stage a real location before it has been surveyed, insert it with
``coordinate_status = 'required'`` and no coordinates:

    INSERT INTO core.campus_location (code, name, location_type)
    VALUES ('LKRC-MAIN', 'Library & Knowledge Resource Centre (LKRC)', 'library');

It stays inactive and invisible to the API until someone stands at the entrance
with a phone and promotes it — see DATABASE_SETUP.md §8. The schema enforces the
discipline: ``coordinate_status = 'required'`` forbids coordinates outright,
``'verified'`` demands a source and a capture time, and ``is_active`` is
impossible without ``'verified'``.

Refuses to run against a database whose name suggests production.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

USERS = [
    ("dev-student-1", "student", "student1@dev.local", None),
    ("dev-student-2", "student", "student2@dev.local", None),
    ("dev-security", "security", "security@dev.local", "Security Desk"),
    ("dev-icc", "icc", "icc@dev.local", "ICC Member"),
    ("dev-admin", "admin", "admin@dev.local", "Administrator"),
]


def resolve_database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        env = pathlib.Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if url and url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url or None


def main() -> int:
    url = resolve_database_url()
    if not url:
        print("DATABASE_URL is not set")
        return 2

    name = url.rsplit("/", 1)[-1].split("?")[0]
    if any(token in name.lower() for token in ("prod", "live")):
        print(f"refusing to seed demo data into {name!r}")
        return 1

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for uid, role, email, display in USERS:
            conn.execute(
                text(
                    "INSERT INTO identity.app_user "
                    "(firebase_uid, role, institutional_email, display_name) "
                    "VALUES (:uid, CAST(:role AS public.user_role), :email, :display) "
                    "ON CONFLICT (firebase_uid) DO NOTHING"
                ),
                {"uid": uid, "role": role, "email": email, "display": display},
            )

        rows = conn.execute(
            text(
                "SELECT firebase_uid, role, user_id FROM identity.app_user "
                "WHERE firebase_uid LIKE 'dev-%' ORDER BY role, firebase_uid"
            )
        ).all()
        categories = conn.execute(
            text("SELECT count(*) FROM core.report_category WHERE is_active")
        ).scalar_one()
        locations = conn.execute(text("SELECT count(*) FROM core.campus_location")).scalar_one()
        verified = conn.execute(
            text("SELECT count(*) FROM core.campus_location WHERE coordinate_status = 'verified'")
        ).scalar_one()

    print(f"\nSeeded into {name}\n")
    print("  Users (send the id as the X-Dev-User header):")
    for uid, role, user_id in rows:
        print(f"    {role:<9} {uid:<16} {user_id}")

    print(f"\n  Active report categories: {categories}")
    if categories == 0:
        print(f"    none — run:  psql -d {name} -f sql/seed_report_categories.sql")

    print(f"\n  Campus locations: {locations} ({verified} with verified coordinates)")
    if locations == 0:
        print("    Empty by design. No coordinates have been verified for Presidency")
        print("    University (see CAMPUS_LOCATIONS.md), and this script will not")
        print("    invent any. POST /api/v1/reports needs an active location, so it")
        print("    will reject submissions until the field survey is loaded.")
    elif verified == 0:
        print("    Staged but unsurveyed — inactive and invisible to the API until")
        print("    coordinates are captured. See DATABASE_SETUP.md §8.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
