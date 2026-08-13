"""Test fixtures.

Tests run against a **real PostgreSQL database** with the real migration
applied. That is deliberate and not negotiable for this project: most of what is
being tested — the anonymity trigger, the append-only audit tables, the CHECK
that keeps anonymous reports uncontactable — exists only in PostgreSQL. Against
SQLite, or against mocks, every one of those tests would pass while the
guarantee was absent.

Each test runs inside a transaction that is rolled back afterwards, so the suite
is order-independent and leaves nothing behind.
"""

from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, scoped_session, sessionmaker

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AppUser, CampusLocation, CampusZone, ReportCategory
from app.models.enums import (
    CoordinateStatus,
    ReportKind,
    UserRole,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_TEST_DB = "postgresql+psycopg://localhost:5432/campusshield_backend_test"


def _test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB)


def _db_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


@pytest.fixture(scope="session")
def database_url() -> str:
    """Create the test database and bring it up to head.

    Alembic is invoked as a subprocess rather than through its Python API so the
    suite exercises the same command a developer runs. If the migration is
    broken, the tests fail here rather than in a confusing way later.
    """
    url = _test_database_url()
    name = _db_name(url)
    if "test" not in name:
        pytest.fail(f"refusing to run against {name!r}: not a test database")

    subprocess.run(["dropdb", "--if-exists", name], check=False, capture_output=True)
    created = subprocess.run(["createdb", name], capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"cannot create test database {name}: {created.stderr.strip()}")

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{migrated.stdout}\n{migrated.stderr}")

    yield url

    subprocess.run(["dropdb", "--if-exists", name], check=False, capture_output=True)


@pytest.fixture(scope="session")
def engine(database_url: str):
    eng = create_engine(database_url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def connection(engine):
    """One connection per test, wrapped in a transaction that is rolled back."""
    conn = engine.connect()
    transaction = conn.begin()
    yield conn
    transaction.rollback()
    conn.close()


@pytest.fixture()
def app(connection, database_url: str):
    config = TestingConfig()
    config.SQLALCHEMY_DATABASE_URI = database_url
    config.AUTH_PROVIDER = "dev"
    config.AUDIT_IP_PEPPER = "test-pepper"
    config.REPORT_TOKEN_PEPPER = "test-pepper"

    flask_app = create_app(config)

    # Bind the app's session to the test's open transaction. Everything the
    # request does — including its commit() — lands inside a transaction the
    # fixture rolls back, so tests never see each other's data.
    #
    # join_transaction_mode="create_savepoint" is what makes a request's commit()
    # survive as a savepoint release rather than ending the outer transaction the
    # fixture still needs to roll back.
    original_session = db.session
    session_factory = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint", future=True
    )
    db.session = scoped_session(session_factory)  # type: ignore[assignment]

    with flask_app.app_context():
        yield flask_app

    db.session.remove()
    db.session = original_session


@pytest.fixture()
def session(app) -> Session:
    return db.session


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def users(session: Session) -> dict[str, AppUser]:
    made = {
        "student": AppUser(
            firebase_uid="test-student-1",
            role=UserRole.STUDENT,
            institutional_email="student1@test.local",
        ),
        "other_student": AppUser(
            firebase_uid="test-student-2",
            role=UserRole.STUDENT,
            institutional_email="student2@test.local",
        ),
        "icc": AppUser(
            firebase_uid="test-icc",
            role=UserRole.ICC,
            institutional_email="icc@test.local",
            display_name="ICC Member",
        ),
        "security": AppUser(
            firebase_uid="test-security",
            role=UserRole.SECURITY,
            institutional_email="security@test.local",
            display_name="Security Desk",
        ),
        "admin": AppUser(
            firebase_uid="test-admin",
            role=UserRole.ADMIN,
            institutional_email="admin@test.local",
            display_name="Administrator",
        ),
    }
    session.add_all(made.values())
    session.flush()
    return made


@pytest.fixture()
def locations(session: Session) -> dict[str, CampusLocation]:
    """Synthetic coordinates.

    0.0/0.0 and 1.0/1.0 are obviously not Presidency University. Real campus
    coordinates do not exist yet — CAMPUS_LOCATIONS.md records zero verified —
    and inventing plausible-looking ones in a fixture is how invented data ends
    up in a seed file.
    """
    zone = CampusZone(code="TEST-ZONE", name="Test Zone")
    session.add(zone)
    session.flush()

    active = CampusLocation(
        code="TEST-ACTIVE",
        name="Test Active Location",
        location_type="academic",
        zone_id=zone.zone_id,
        latitude=0.0,
        longitude=0.0,
        coordinate_status=CoordinateStatus.VERIFIED,
        coordinate_source="synthetic test fixture",
        coordinate_captured_at=datetime.now(timezone.utc),
        is_active=True,
    )
    second = CampusLocation(
        code="TEST-ACTIVE-2",
        name="Second Active Location",
        location_type="parking",
        zone_id=zone.zone_id,
        latitude=1.0,
        longitude=1.0,
        coordinate_status=CoordinateStatus.VERIFIED,
        coordinate_source="synthetic test fixture",
        coordinate_captured_at=datetime.now(timezone.utc),
        is_active=True,
    )
    unsurveyed = CampusLocation(
        code="TEST-UNSURVEYED",
        name="Unsurveyed Location",
        location_type="gate",
        zone_id=zone.zone_id,
    )
    session.add_all([active, second, unsurveyed])
    session.flush()
    return {"active": active, "second": second, "unsurveyed": unsurveyed}


@pytest.fixture()
def categories(session: Session) -> dict[str, ReportCategory]:
    harassment = ReportCategory(
        code="TEST_HARASS",
        label="Test harassment",
        kind=ReportKind.INCIDENT,
        routes_to_role=UserRole.ICC,
        base_severity=4,
        requires_confidentiality=True,
        emergency_eligible=True,
    )
    lighting = ReportCategory(
        code="TEST_LIGHTING",
        label="Test poor lighting",
        kind=ReportKind.CONCERN,
        routes_to_role=UserRole.SECURITY,
        base_severity=2,
        requires_confidentiality=False,
        emergency_eligible=False,
    )
    inactive = ReportCategory(
        code="TEST_RETIRED",
        label="Retired category",
        kind=ReportKind.INCIDENT,
        routes_to_role=UserRole.ICC,
        base_severity=2,
        is_active=False,
    )
    session.add_all([harassment, lighting, inactive])
    session.flush()
    return {"harassment": harassment, "lighting": lighting, "inactive": inactive}


@pytest.fixture()
def occurred_at() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=2)


def auth(user: AppUser) -> dict[str, str]:
    """Development-auth header for a user."""
    return {"X-Dev-User": str(user.user_id)}


@pytest.fixture()
def report_payload(locations, categories, occurred_at):
    def build(**overrides):
        payload = {
            "category_id": categories["harassment"].category_id,
            "location_id": locations["active"].location_id,
            "occurred_at": occurred_at.isoformat(),
            "narrative": "A test account of an incident, long enough to be valid.",
        }
        payload.update(overrides)
        return payload

    return build


def report_id_for(session: Session, public_ref: str) -> uuid.UUID:
    return session.scalar(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"),
        {"ref": public_ref},
    )


# ---------------------------------------------------------------------------
# Evidence helpers
#
# Real image bytes and the real upload endpoint. Report tests that need an
# attached image stage it the way a browser does — there is no way to fabricate
# an evidence row from a payload any more, which is the point of 4B-1.
# ---------------------------------------------------------------------------


def make_image(
    fmt: str = "JPEG",
    size: tuple[int, int] = (320, 240),
    *,
    with_exif: bool = False,
    mode: str = "RGB",
) -> bytes:
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    image = Image.new(mode, size, (120, 30, 30) if mode == "RGB" else (120, 30, 30, 255))
    buffer = io.BytesIO()

    if with_exif and fmt == "JPEG":
        exif = Image.Exif()
        exif[0x010F] = "TestCameraMake"
        exif[0x0110] = "TestCameraModel"
        exif[0x0131] = "CampusShieldTestOS 1.0"
        exif[0x9003] = "2026:08:10 20:14:00"
        # Deliberately in the South Atlantic, nowhere near any real campus —
        # this project never writes a real-looking coordinate anywhere,
        # including a fixture whose only job is to prove GPS gets stripped.
        exif.get_ifd(0x8825).update(
            {
                1: "S",
                2: (IFDRational(1, 1), IFDRational(15, 1), IFDRational(30, 1)),
                3: "W",
                4: (IFDRational(2, 1), IFDRational(30, 1), IFDRational(45, 1)),
            }
        )
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()


def upload(client, user, data: bytes, *, filename="photo.jpg", content_type="image/jpeg"):
    """POST an image to the real upload endpoint."""
    return client.post(
        "/api/v1/evidence",
        data={"file": (io.BytesIO(data), filename, content_type)},
        content_type="multipart/form-data",
        headers=auth(user),
    )


def staged_token(client, user, **kwargs) -> str:
    """Upload an image and return the capability token for it."""
    response = upload(client, user, make_image(), **kwargs)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["upload_token"]


@pytest.fixture()
def storage(app):
    return app.extensions["storage_provider"]
