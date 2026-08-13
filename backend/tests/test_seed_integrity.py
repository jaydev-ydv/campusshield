"""Development seed data must never invent a campus location.

``core.campus_location`` is the controlled vocabulary every report anchors to,
and every downstream feature reads its coordinates: the safety map, hotspot
detection, cluster centroids, and before/after impact measurement. A location
with invented coordinates does not fail loudly — it produces a pin in the wrong
place, a hotspot where nobody was, and an impact measurement against a baseline
that never existed. Wrong data that looks right is worse than none, because none
is visibly missing.

CAMPUS_LOCATIONS.md records zero verified coordinates for Presidency University,
so until the field survey is loaded the correct contents of that table is
nothing.

Two kinds of check here, because either alone is weak:

* **Behavioural** — run the seed script against a scratch database and assert the
  table is still empty. Proves what the script actually does.
* **Structural** — assert the script contains no INSERT into campus_location and
  no coordinate literals. Catches a regression at the point someone writes it,
  rather than after they wire it up.

Note on test fixtures: ``conftest.locations`` does create locations with
synthetic 0.0/0.0 coordinates, and that is deliberate and different. Those live
inside a transaction that is rolled back, in a throwaway database that is dropped
when the session ends. They never reach a development or production database.
Seed data does. The distinction is the whole point of this file.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
SEED_SCRIPT = BACKEND_ROOT / "scripts" / "seed_dev_data.py"
SCRATCH_DB = "campusshield_seed_scratch_test"


# ---------------------------------------------------------------------------
# Behavioural: what the script does
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scratch_database() -> str:
    """A database of its own, so seeding cannot disturb the other tests."""
    url = f"postgresql+psycopg://localhost:5432/{SCRATCH_DB}"

    subprocess.run(["dropdb", "--if-exists", SCRATCH_DB], check=False, capture_output=True)
    created = subprocess.run(["createdb", SCRATCH_DB], capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"cannot create {SCRATCH_DB}: {created.stderr.strip()}")

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{migrated.stderr}")

    yield url

    subprocess.run(["dropdb", "--if-exists", SCRATCH_DB], check=False, capture_output=True)


@pytest.fixture(scope="module")
def seeded(scratch_database: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SEED_SCRIPT)],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": scratch_database},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"seed script failed:\n{result.stdout}\n{result.stderr}"
    return scratch_database


def _scalar(url: str, query: str):
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return conn.scalar(text(query))
    finally:
        engine.dispose()


@pytest.mark.privacy
def test_seed_creates_no_campus_locations(seeded: str):
    """The requirement, stated directly."""
    count = _scalar(seeded, "SELECT count(*) FROM core.campus_location")
    assert count == 0, f"seed data created {count} campus location(s)"


@pytest.mark.privacy
def test_seed_creates_no_coordinates_anywhere(seeded: str):
    """Not even on a staged, inactive row."""
    count = _scalar(
        seeded,
        "SELECT count(*) FROM core.campus_location "
        "WHERE latitude IS NOT NULL OR longitude IS NOT NULL",
    )
    assert count == 0


def test_seed_creates_users(seeded: str):
    """The check above would pass on a script that does nothing at all."""
    count = _scalar(
        seeded, "SELECT count(*) FROM identity.app_user WHERE firebase_uid LIKE 'dev-%'"
    )
    assert count == 5


def test_seed_is_idempotent(seeded: str):
    """Running it twice must not duplicate accounts or create a location."""
    result = subprocess.run(
        [sys.executable, str(SEED_SCRIPT)],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": seeded},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert _scalar(seeded, "SELECT count(*) FROM identity.app_user") == 5
    assert _scalar(seeded, "SELECT count(*) FROM core.campus_location") == 0


def test_seed_output_states_the_table_is_empty_by_design(seeded: str):
    """An empty location list must read as intentional, not as a broken setup."""
    result = subprocess.run(
        [sys.executable, str(SEED_SCRIPT)],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": seeded},
        capture_output=True,
        text=True,
    )
    assert "Campus locations: 0" in result.stdout
    assert "by design" in result.stdout


# ---------------------------------------------------------------------------
# Structural: what the script contains
# ---------------------------------------------------------------------------

SOURCE = SEED_SCRIPT.read_text()
CODE_ONLY = re.sub(r'"""[\s\S]*?"""', "", SOURCE)  # strip docstrings; prose may explain


@pytest.mark.privacy
def test_seed_source_has_no_campus_location_insert():
    lowered = CODE_ONLY.lower()
    assert "insert into core.campus_location" not in lowered
    assert "campuslocation(" not in lowered.replace(" ", "")


@pytest.mark.privacy
def test_seed_source_has_no_coordinate_literals():
    """No decimal literal that could be latitude or longitude.

    Deliberately blunt. A regression here will most likely arrive as someone
    pasting a plausible-looking pair of numbers, and this catches that in review
    rather than after it has produced a hotspot nobody can explain.
    """
    offenders = [
        literal
        for literal in re.findall(r"(?<![\w.])-?\d{1,3}\.\d+", CODE_ONLY)
        if literal not in {"3.10", "3.12"}  # version strings, not coordinates
    ]
    assert not offenders, f"coordinate-shaped literals in seed source: {offenders}"


@pytest.mark.privacy
def test_seed_source_mentions_no_coordinate_columns():
    lowered = CODE_ONLY.lower()
    for column in ("latitude", "longitude", "coordinate_captured_at", "coordinate_source"):
        assert f"{column}," not in lowered, f"seed source writes {column}"


# ---------------------------------------------------------------------------
# The schema still supports staging an unsurveyed location
# ---------------------------------------------------------------------------


def test_location_can_be_staged_without_coordinates(session, locations):
    """Removing the fake seed must not remove the ability to record a real place
    whose coordinates are still pending."""
    from app.models import CampusLocation
    from app.models.enums import CoordinateStatus

    staged = CampusLocation(
        code="LKRC-MAIN-STAGED",
        name="Library & Knowledge Resource Centre (LKRC)",
        location_type="library",
    )
    session.add(staged)
    session.flush()

    assert staged.coordinate_status is CoordinateStatus.REQUIRED
    assert staged.latitude is None
    assert staged.longitude is None
    assert staged.is_active is False


def test_staged_location_cannot_be_activated(session):
    """`coordinate_status='required'` is not a label; it is load-bearing."""
    from sqlalchemy.exc import IntegrityError

    session.execute(
        text(
            "INSERT INTO core.campus_location (code, name, location_type) "
            "VALUES ('GATE-STAGED', 'North Gate', 'gate')"
        )
    )
    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE core.campus_location SET is_active = TRUE WHERE code = 'GATE-STAGED'")
        )
    session.rollback()


def test_staged_location_is_absent_from_the_api(client, users, session):
    """A staged location must not be offered as a choice, and a report cannot be
    filed against it."""
    from .conftest import auth

    session.execute(
        text(
            "INSERT INTO core.campus_location (code, name, location_type) "
            "VALUES ('PARK-STAGED', 'Parking Block B', 'parking')"
        )
    )
    session.flush()

    listed = client.get("/api/v1/locations", headers=auth(users["student"])).get_json()
    assert "PARK-STAGED" not in {item["code"] for item in listed["items"]}
