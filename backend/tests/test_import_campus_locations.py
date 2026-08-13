"""``scripts/import_campus_locations.py`` — parsing, validation, and writes.

Parsing/validation tests need no database and run against in-memory CSV
text. The write tests run against the real test database (the same one
every other test in this suite uses — see `conftest.py`), because the
question that matters is "does a commit actually land the right row," not
"does the function return the right dataclass." Every row this file writes
uses an `IMPTEST-`/`ZTEST-` code prefix and is deleted in a fixture teardown
that runs whether the test passed or failed, so nothing here outlives the
test even though the writes are real commits the per-test transaction
rollback (used everywhere else in this suite) cannot undo.
"""

from __future__ import annotations

import csv
import pathlib

import pytest
from sqlalchemy import create_engine, text

from scripts.import_campus_locations import (
    ImportValidationError,
    LocationRow,
    ZoneRow,
    parse_locations,
    parse_zones,
    resolve_database_url,
    run,
)

LOCATION_HEADER = [
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
ZONE_HEADER = ["code", "name", "description", "responsible_role"]


def _write_csv(
    tmp_path: pathlib.Path, name: str, header: list[str], rows: list[dict]
) -> pathlib.Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in header})
    return path


def _location_row(**overrides) -> dict:
    row = dict.fromkeys(LOCATION_HEADER, "")
    row["code"] = "A-1"
    row["name"] = "Place A"
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Parsing and validation — no database
# ---------------------------------------------------------------------------


def test_a_minimal_staged_row_parses(tmp_path):
    path = _write_csv(tmp_path, "locations.csv", LOCATION_HEADER, [_location_row()])
    rows = parse_locations(path)
    assert rows == [
        LocationRow(
            code="A-1",
            name="Place A",
            location_type=None,
            zone_code=None,
            latitude=None,
            longitude=None,
            coordinate_status="required",
            coordinate_source=None,
            coordinate_captured_at=None,
            is_indoor=None,
            has_lighting=None,
            has_cctv=None,
            footfall_band=None,
            dispatch_note=None,
            is_active=False,
        )
    ]


def test_a_full_verified_row_parses(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [
            _location_row(
                location_type="library",
                latitude="12.5",
                longitude="77.5",
                coordinate_status="verified",
                coordinate_source="field survey",
                coordinate_captured_at="2026-01-01T09:00:00+05:30",
                is_indoor="true",
                has_lighting="true",
                has_cctv="false",
                footfall_band="high",
                dispatch_note="Main door",
                is_active="true",
            )
        ],
    )
    row = parse_locations(path)[0]
    assert row.coordinate_status == "verified"
    assert row.is_active is True
    assert row.is_indoor is True
    assert row.has_cctv is False
    assert str(row.latitude) == "12.500000"
    assert row.coordinate_captured_at is not None
    assert row.coordinate_captured_at.utcoffset() is not None


def test_missing_code_is_rejected(tmp_path):
    path = _write_csv(tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(code="")])
    with pytest.raises(ImportValidationError, match="code is required"):
        parse_locations(path)


def test_missing_name_is_rejected(tmp_path):
    path = _write_csv(tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(name="")])
    with pytest.raises(ImportValidationError, match="name is required"):
        parse_locations(path)


def test_missing_required_column_is_rejected(tmp_path):
    header = [c for c in LOCATION_HEADER if c != "coordinate_status"]
    path = tmp_path / "locations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerow({c: "" for c in header if c != "code"} | {"code": "A-1", "name": "x"})
    with pytest.raises(ImportValidationError, match="missing required column"):
        parse_locations(path)


def test_duplicate_code_within_file_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(code="A-1", name="One"), _location_row(code="A-1", name="Two")],
    )
    with pytest.raises(ImportValidationError, match="duplicate code"):
        parse_locations(path)


def test_duplicate_name_within_file_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [
            _location_row(code="A-1", name="Main Cafeteria"),
            _location_row(code="A-2", name="  main   cafeteria  "),
        ],
    )
    with pytest.raises(ImportValidationError, match="duplicates row"):
        parse_locations(path)


def test_unknown_location_type_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(location_type="not_real")]
    )
    with pytest.raises(ImportValidationError, match="location_type"):
        parse_locations(path)


def test_unknown_footfall_band_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(footfall_band="extreme")]
    )
    with pytest.raises(ImportValidationError, match="footfall_band"):
        parse_locations(path)


def test_latitude_without_longitude_is_rejected(tmp_path):
    path = _write_csv(tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(latitude="12.5")])
    with pytest.raises(ImportValidationError, match="both be present or both absent"):
        parse_locations(path)


@pytest.mark.parametrize(
    "field,value",
    [("latitude", "999"), ("latitude", "-91"), ("longitude", "181"), ("longitude", "-200")],
)
def test_out_of_range_coordinate_is_rejected(tmp_path, field, value):
    coords = {"latitude": "12.5", "longitude": "77.5"}
    coords[field] = value
    row = _location_row(**coords)
    path = _write_csv(tmp_path, "locations.csv", LOCATION_HEADER, [row])
    with pytest.raises(ImportValidationError, match="out of range"):
        parse_locations(path)


def test_non_numeric_coordinate_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(latitude="not-a-number", longitude="77.5")],
    )
    with pytest.raises(ImportValidationError, match="not a valid decimal"):
        parse_locations(path)


def test_required_status_forbids_coordinates(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(latitude="12.5", longitude="77.5", coordinate_status="required")],
    )
    with pytest.raises(ImportValidationError, match="ck_campus_location_required_has_no_coords"):
        parse_locations(path)


@pytest.mark.parametrize(
    "missing_field",
    ["coordinate_source", "coordinate_captured_at"],
)
def test_verified_status_requires_full_sourcing(tmp_path, missing_field):
    fields = {
        "latitude": "12.5",
        "longitude": "77.5",
        "coordinate_status": "verified",
        "coordinate_source": "field survey",
        "coordinate_captured_at": "2026-01-01T09:00:00+05:30",
    }
    fields[missing_field] = ""
    path = _write_csv(tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(**fields)])
    with pytest.raises(ImportValidationError, match="ck_campus_location_verified_is_sourced"):
        parse_locations(path)


def test_naive_captured_at_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [
            _location_row(
                latitude="12.5",
                longitude="77.5",
                coordinate_status="verified",
                coordinate_source="field survey",
                coordinate_captured_at="2026-01-01 09:00:00",
            )
        ],
    )
    with pytest.raises(ImportValidationError, match="no UTC offset"):
        parse_locations(path)


def test_active_without_verified_status_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(coordinate_status="provisional", is_active="true")],
    )
    with pytest.raises(ImportValidationError, match="ck_campus_location_active_requires_verified"):
        parse_locations(path)


def test_duplicate_coordinate_within_file_is_rejected(tmp_path):
    shared = {
        "latitude": "12.5",
        "longitude": "77.5",
        "coordinate_status": "verified",
        "coordinate_source": "field survey",
        "coordinate_captured_at": "2026-01-01T09:00:00+05:30",
    }
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [
            _location_row(code="A-1", name="Place A", **shared),
            _location_row(code="A-2", name="Place A Annexe", **shared),
        ],
    )
    with pytest.raises(ImportValidationError, match="duplicates row 2 exactly"):
        parse_locations(path)


def test_invalid_bool_field_is_rejected(tmp_path):
    path = _write_csv(
        tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(is_indoor="maybe")]
    )
    with pytest.raises(ImportValidationError, match="must be true/false"):
        parse_locations(path)


def test_zones_reject_student_as_responsible_role(tmp_path):
    path = _write_csv(
        tmp_path,
        "zones.csv",
        ZONE_HEADER,
        [{"code": "Z-1", "name": "Zone One", "description": "", "responsible_role": "student"}],
    )
    with pytest.raises(ImportValidationError, match="ck_campus_zone_role"):
        parse_zones(path)


def test_zones_reject_unknown_role(tmp_path):
    path = _write_csv(
        tmp_path,
        "zones.csv",
        ZONE_HEADER,
        [{"code": "Z-1", "name": "Zone One", "description": "", "responsible_role": "superadmin"}],
    )
    with pytest.raises(ImportValidationError, match="responsible_role"):
        parse_zones(path)


def test_zones_parse_cleanly(tmp_path):
    path = _write_csv(
        tmp_path,
        "zones.csv",
        ZONE_HEADER,
        [
            {
                "code": "Z-1",
                "name": "Zone One",
                "description": "A zone",
                "responsible_role": "security",
            }
        ],
    )
    assert parse_zones(path) == [
        ZoneRow(code="Z-1", name="Zone One", description="A zone", responsible_role="security")
    ]


def test_resolve_database_url_prefers_explicit_argument(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example/from-env")
    assert resolve_database_url("postgresql+psycopg://example/explicit") == (
        "postgresql+psycopg://example/explicit"
    )


def test_resolve_database_url_normalises_the_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    assert resolve_database_url(None) == "postgresql+psycopg://example/db"


# ---------------------------------------------------------------------------
# Writes — the real test database
# ---------------------------------------------------------------------------


@pytest.fixture()
def importer_cleanup(database_url):
    """Delete every `IMPTEST-`/`ZTEST-` row a test wrote, whether it passed
    or failed. Runs through its own connection: the importer commits through
    its own engine too, so the per-test rollback the rest of this suite
    relies on (`conftest.py`'s `connection` fixture) never touches these
    rows — this fixture is what actually removes them.
    """
    yield
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM core.campus_location WHERE code LIKE 'IMPTEST-%'"))
        conn.execute(text("DELETE FROM core.campus_zone WHERE code LIKE 'ZTEST-%'"))
    engine.dispose()


def test_a_commit_writes_the_row_exactly_as_given(
    tmp_path, database_url, session, importer_cleanup
):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [
            _location_row(
                code="IMPTEST-1",
                name="Import Test Location",
                location_type="library",
                latitude="12.5",
                longitude="77.5",
                coordinate_status="verified",
                coordinate_source="field survey, GPS, main entrance",
                coordinate_captured_at="2026-01-01T09:00:00+05:30",
                is_indoor="true",
                has_lighting="true",
                has_cctv="false",
                footfall_band="high",
                dispatch_note="Enter via the service gate",
                is_active="true",
            )
        ],
    )
    exit_code = run(locations_path=path, zones_path=None, database_url=database_url, commit=True)
    assert exit_code == 0

    row = session.execute(
        text(
            "SELECT name, location_type, latitude, longitude, coordinate_status, "
            "coordinate_source, is_indoor, has_lighting, has_cctv, footfall_band, "
            "dispatch_note, is_active "
            "FROM core.campus_location WHERE code = 'IMPTEST-1'"
        )
    ).one()
    assert row.name == "Import Test Location"
    assert row.location_type == "library"
    assert float(row.latitude) == 12.5
    assert float(row.longitude) == 77.5
    assert row.coordinate_status == "verified"
    assert row.is_indoor is True
    assert row.has_cctv is False
    assert row.footfall_band == "high"
    assert row.is_active is True


def test_a_dry_run_writes_nothing(tmp_path, database_url, session, importer_cleanup):
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(code="IMPTEST-2", name="Dry Run")],
    )
    exit_code = run(locations_path=path, zones_path=None, database_url=database_url, commit=False)
    assert exit_code == 0

    count = session.execute(
        text("SELECT count(*) FROM core.campus_location WHERE code = 'IMPTEST-2'")
    ).scalar_one()
    assert count == 0


def test_a_second_run_skips_the_already_imported_code(
    tmp_path, database_url, session, importer_cleanup, capsys
):
    path = _write_csv(
        tmp_path, "locations.csv", LOCATION_HEADER, [_location_row(code="IMPTEST-3", name="Once")]
    )
    run(locations_path=path, zones_path=None, database_url=database_url, commit=True)
    capsys.readouterr()

    run(locations_path=path, zones_path=None, database_url=database_url, commit=True)
    out = capsys.readouterr().out
    assert "0 to insert, 1 already present (skipped)" in out

    count = session.execute(
        text("SELECT count(*) FROM core.campus_location WHERE code = 'IMPTEST-3'")
    ).scalar_one()
    assert count == 1


def test_zones_are_created_and_locations_resolve_against_them(
    tmp_path, database_url, session, importer_cleanup
):
    zones_path = _write_csv(
        tmp_path,
        "zones.csv",
        ZONE_HEADER,
        [
            {
                "code": "ZTEST-1",
                "name": "Import Test Zone",
                "description": "",
                "responsible_role": "",
            }
        ],
    )
    locations_path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(code="IMPTEST-4", name="Zoned Location", zone_code="ZTEST-1")],
    )
    run(
        locations_path=locations_path, zones_path=zones_path, database_url=database_url, commit=True
    )

    row = session.execute(
        text(
            "SELECT z.code FROM core.campus_location l "
            "JOIN core.campus_zone z ON z.zone_id = l.zone_id "
            "WHERE l.code = 'IMPTEST-4'"
        )
    ).one()
    assert row.code == "ZTEST-1"


def test_an_unresolvable_zone_reference_aborts_before_any_write(
    tmp_path, database_url, session, importer_cleanup
):
    locations_path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [_location_row(code="IMPTEST-5", name="Orphaned", zone_code="ZTEST-DOES-NOT-EXIST")],
    )
    with pytest.raises(ImportValidationError, match="zone_code"):
        run(locations_path=locations_path, zones_path=None, database_url=database_url, commit=True)

    count = session.execute(
        text("SELECT count(*) FROM core.campus_location WHERE code = 'IMPTEST-5'")
    ).scalar_one()
    assert count == 0


def test_a_verified_row_is_immediately_visible_to_the_real_catalogue_query(
    tmp_path, database_url, session, importer_cleanup
):
    """Not a re-test of `LocationRepository` — a check that a row this
    importer writes is actually shaped the way that repository expects,
    end to end through real SQL rather than through the ORM the importer
    deliberately does not use."""
    path = _write_csv(
        tmp_path,
        "locations.csv",
        LOCATION_HEADER,
        [
            _location_row(
                code="IMPTEST-6",
                name="Visible Location",
                latitude="12.5",
                longitude="77.5",
                coordinate_status="verified",
                coordinate_source="field survey",
                coordinate_captured_at="2026-01-01T09:00:00+05:30",
                is_active="true",
            )
        ],
    )
    run(locations_path=path, zones_path=None, database_url=database_url, commit=True)

    active_codes = (
        session.execute(text("SELECT code FROM core.campus_location WHERE is_active"))
        .scalars()
        .all()
    )
    assert "IMPTEST-6" in active_codes
