"""Request-rate abuse controls (Phase 4G).

`RATELIMIT_ENABLED=False` in `TestingConfig` keeps the rest of the suite —
hundreds of tests sharing one process — from tripping over each other's
request counts. These tests build their own app with it explicitly turned
back on, each getting a fresh in-memory counter store: `Limiter.init_app`
constructs a brand-new storage object on every call, confirmed by reading
`flask_limiter`'s own source rather than assumed, so one test's requests
never count against another's.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from app import create_app
from app.extensions import db

from .conftest import auth, make_image


@pytest.fixture()
def limited_app(connection, database_url: str):
    from app.config import TestingConfig

    config = TestingConfig()
    config.SQLALCHEMY_DATABASE_URI = database_url
    config.AUTH_PROVIDER = "dev"
    config.AUDIT_IP_PEPPER = "test-pepper"
    config.REPORT_TOKEN_PEPPER = "test-pepper"
    config.RATELIMIT_ENABLED = True

    flask_app = create_app(config)

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
def limited_client(limited_app):
    return limited_app.test_client()


@pytest.fixture()
def limited_session(limited_app) -> Session:
    return db.session


def test_the_global_default_limit_eventually_returns_429(limited_client):
    """30 per minute is the tightest global default. A public, unauthenticated
    endpoint is used so nothing about auth affects the count."""
    statuses = [limited_client.get("/api/v1/health").status_code for _ in range(35)]

    assert 200 in statuses
    assert 429 in statuses


def test_a_429_response_still_carries_the_baseline_security_headers(limited_client):
    for _ in range(35):
        response = limited_client.get("/api/v1/health")
        if response.status_code == 429:
            break
    else:
        pytest.fail("never hit the limit")

    assert response.headers["X-Frame-Options"] == "DENY"


def test_registration_has_a_stricter_limit_than_the_global_default(limited_client):
    """10 per hour on /auth/register, tighter than the 30-per-minute global
    default — proven by hitting it well under 30 calls."""
    statuses = [
        limited_client.post(
            "/api/v1/auth/register",
            json={},
            headers={"X-Dev-User": f"rate-limit-probe-{i}"},
        ).status_code
        for i in range(15)
    ]

    assert 429 in statuses


def test_evidence_upload_has_a_stricter_limit_than_the_global_default(
    limited_client, limited_session
):
    """30 per hour on POST /evidence. Uses one authenticated user across every
    call — the limit is keyed on remote address, not on the account, so this
    still exercises the per-route limit rather than the global one."""
    from app.models import AppUser
    from app.models.enums import UserRole

    user = AppUser(
        firebase_uid="rate-limit-uploader",
        role=UserRole.STUDENT,
        institutional_email="rate-limit-uploader@test.local",
    )
    limited_session.add(user)
    limited_session.flush()

    def do_upload():
        return limited_client.post(
            "/api/v1/evidence",
            data={"file": (io.BytesIO(make_image()), "x.jpg", "image/jpeg")},
            content_type="multipart/form-data",
            headers=auth(user),
        ).status_code

    statuses = [do_upload() for _ in range(35)]

    assert 429 in statuses
    # And it trips strictly before the global 200-per-hour ceiling could
    # explain it — 35 calls is under that, so only the route-specific 30/hour
    # limit can be responsible.
    assert statuses.count(429) >= 1


def test_rate_limiting_is_off_by_default_in_tests(client):
    """The ordinary `client`/`app` fixtures (RATELIMIT_ENABLED=False) must not
    throttle the rest of the suite — this is the guarantee every other test
    file quietly depends on."""
    statuses = [client.get("/api/v1/health").status_code for _ in range(40)]

    assert all(status == 200 for status in statuses)
