"""``scripts/seed_demo_campus_locations.py`` — demo data, and only demo data.

Mirrors the structure of `test_seed_integrity.py` deliberately: that file
proves `seed_dev_data.py` never creates a campus location at all. This file
proves the complementary thing — the one script that *does* create campus
locations for development marks every single row it writes as synthetic,
refuses to touch anything that looks like production, and is safely
re-runnable. Neither file's guarantee substitutes for the other's.
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
DEMO_SCRIPT = BACKEND_ROOT / "scripts" / "seed_demo_campus_locations.py"
IMPORT_SCRIPT = BACKEND_ROOT / "scripts" / "import_campus_locations.py"
SCRATCH_DB = "campusshield_demo_seed_scratch_test"


# ---------------------------------------------------------------------------
# Behavioural: what the script does
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scratch_database() -> str:
    """A database of its own, so seeding cannot disturb the other tests."""
    from tests.conftest import _drop_db, _recreate_db, _test_database_url

    url = _test_database_url().rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    try:
        _recreate_db(url, SCRATCH_DB)
    except Exception as exc:
        pytest.skip(f"cannot create {SCRATCH_DB}: {exc}")

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={url}", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{migrated.stderr}")

    yield url

    _drop_db(url, SCRATCH_DB)


@pytest.fixture(scope="module")
def seeded(scratch_database: str) -> str:
    result = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT)],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": scratch_database},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"demo seed script failed:\n{result.stdout}\n{result.stderr}"
    return scratch_database


def _scalar(url: str, query: str):
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return conn.scalar(text(query))
    finally:
        engine.dispose()


def test_demo_seed_creates_locations(seeded: str):
    count = _scalar(
        seeded, "SELECT count(*) FROM core.campus_location WHERE code <> 'SYS-UNSPECIFIED'"
    )
    assert count == 5


def test_every_demo_location_is_flagged_synthetic(seeded: str):
    """Excludes 'SYS-UNSPECIFIED': the permanent emergency-path sentinel from
    migration 0007, not a demo fixture, so `is_synthetic` is correctly false
    for it."""
    count = _scalar(
        seeded,
        "SELECT count(*) FROM core.campus_location "
        "WHERE NOT is_synthetic AND code <> 'SYS-UNSPECIFIED'",
    )
    assert count == 0, "a demo-seeded row was not flagged is_synthetic"


def test_every_demo_location_carries_the_required_source_prefix(seeded: str):
    count = _scalar(
        seeded,
        "SELECT count(*) FROM core.campus_location "
        "WHERE coordinate_source NOT LIKE 'DEMO FIXTURE:%'",
    )
    assert count == 0


def test_every_demo_location_is_active_and_mapped(seeded: str):
    """The whole point: demo data must actually work, not just exist.

    Excludes 'SYS-UNSPECIFIED', which is deliberately inactive and unmapped —
    see migration 0007 — and is not a demo fixture this script is responsible
    for.
    """
    count = _scalar(
        seeded,
        "SELECT count(*) FROM core.campus_location "
        "WHERE (NOT is_active OR coordinate_status <> 'verified' "
        "OR latitude IS NULL OR longitude IS NULL) AND code <> 'SYS-UNSPECIFIED'",
    )
    assert count == 0


def test_demo_seed_is_idempotent(seeded: str):
    result = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT)],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": seeded},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "0 inserted, 5 already present" in result.stdout
    assert (
        _scalar(seeded, "SELECT count(*) FROM core.campus_location WHERE code <> 'SYS-UNSPECIFIED'")
        == 5
    )


def test_demo_seed_refuses_a_production_looking_database_name():
    result = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT)],
        cwd=BACKEND_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+psycopg://localhost:5432/campusshield_production",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "refusing" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Structural: the real-data importer can never produce a demo row
# ---------------------------------------------------------------------------

IMPORT_SOURCE = IMPORT_SCRIPT.read_text()
IMPORT_CODE_ONLY = re.sub(r'"""[\s\S]*?"""', "", IMPORT_SOURCE)


@pytest.mark.privacy
def test_the_real_data_importer_never_mentions_is_synthetic():
    """`import_campus_locations.py` loads real survey data. It must have no
    code path — not a column, not a CLI flag, not a CSV field — capable of
    setting `is_synthetic`. Structural, so a regression is caught at the
    point someone adds the column to that script, not after."""
    assert "is_synthetic" not in IMPORT_CODE_ONLY


@pytest.mark.privacy
def test_a_row_from_the_real_importer_defaults_to_not_synthetic(tmp_path, database_url, session):
    """Behavioural companion to the structural check above: an actual
    committed row from the real importer is `is_synthetic = false` by the
    column's own default, not merely by the importer's silence about it."""
    import csv

    from scripts.import_campus_locations import run

    header = [
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
    path = tmp_path / "real_locations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerow(
            {
                **dict.fromkeys(header, ""),
                "code": "IMPTEST-SYNTHFLAG",
                "name": "Import Test Synthetic Flag Check",
            }
        )

    try:
        run(locations_path=path, zones_path=None, database_url=database_url, commit=True)
        row = session.execute(
            text("SELECT is_synthetic FROM core.campus_location WHERE code = 'IMPTEST-SYNTHFLAG'")
        ).scalar_one()
        assert row is False
    finally:
        engine = create_engine(database_url, future=True)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM core.campus_location WHERE code = 'IMPTEST-SYNTHFLAG'"))
        engine.dispose()
