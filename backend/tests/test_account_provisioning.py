"""Account provisioning and session identity.

Registration is the one place a client can cause a row to appear in
``identity.app_user``, which makes it the one place a client could try to give
itself a role. Most of what is here exists to prove it cannot.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from .conftest import auth


def register(client, uid: str, body: dict | None = None):
    return client.post("/api/v1/auth/register", json=body or {}, headers={"X-Dev-User": uid})


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def test_register_creates_an_application_account(client, session):
    response = register(client, "new-firebase-uid", {"email": "newcomer@test.local"})
    assert response.status_code == 201

    body = response.get_json()
    assert body["created"] is True
    assert body["email"] == "newcomer@test.local"
    assert body["role"] == "student"

    stored = session.scalar(
        text("SELECT role::text FROM identity.app_user WHERE firebase_uid = :uid"),
        {"uid": "new-firebase-uid"},
    )
    assert stored == "student"


def test_register_is_idempotent(client):
    """A client whose response was lost will retry. Telling it the account
    already exists, when from its point of view nothing happened, turns a
    successful sign-up into a dead end."""
    first = register(client, "retry-uid", {"email": "retry@test.local"})
    second = register(client, "retry-uid", {"email": "retry@test.local"})

    assert first.status_code == 201
    assert first.get_json()["created"] is True
    assert second.status_code == 200
    assert second.get_json()["created"] is False
    assert first.get_json()["user_id"] == second.get_json()["user_id"]


def test_register_requires_a_credential(client):
    response = client.post("/api/v1/auth/register", json={"email": "x@test.local"})
    assert response.status_code == 401


def test_register_rejects_a_duplicate_email(client, users):
    """A different credential claiming an existing address would be an account
    takeover, not a sign-up."""
    response = register(client, "different-uid", {"email": users["student"].institutional_email})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "CONFLICT"


def test_register_rejects_a_malformed_email(client):
    response = register(client, "bad-email-uid", {"email": "not-an-address"})
    assert response.status_code == 400


def test_register_requires_an_email_when_the_credential_carries_none(client):
    response = register(client, "no-email-uid", {})
    assert response.status_code == 400
    assert "email" in response.get_json()["error"]["details"]["fields"]


# ---------------------------------------------------------------------------
# The client cannot choose its own role
# ---------------------------------------------------------------------------


@pytest.mark.privacy
@pytest.mark.parametrize("field", ["role", "user_role", "is_admin"])
def test_register_rejects_any_attempt_to_set_a_role(client, field):
    """Rejected outright rather than ignored.

    Silently dropping the field would let a client believe it had been honoured,
    and would leave no signal that someone tried.
    """
    response = register(
        client, f"role-attempt-{field}", {"email": "attempt@test.local", field: "admin"}
    )
    assert response.status_code == 400


@pytest.mark.privacy
def test_self_provisioned_accounts_are_always_students(client, session):
    for i in range(3):
        register(client, f"bulk-uid-{i}", {"email": f"bulk{i}@test.local"})

    roles = (
        session.execute(
            text(
                "SELECT DISTINCT role::text FROM identity.app_user WHERE firebase_uid LIKE 'bulk-%'"
            )
        )
        .scalars()
        .all()
    )
    assert roles == ["student"]


@pytest.mark.privacy
def test_students_are_provisioned_without_a_name(client, session):
    """A CHECK constraint forbids it, and nothing in the project needs one."""
    register(client, "nameless-uid", {"email": "nameless@test.local"})
    name = session.scalar(
        text("SELECT display_name FROM identity.app_user WHERE firebase_uid = :uid"),
        {"uid": "nameless-uid"},
    )
    assert name is None


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------


def test_me_returns_the_callers_identity(client, users):
    response = client.get("/api/v1/auth/me", headers=auth(users["icc"]))
    assert response.status_code == 200

    body = response.get_json()
    assert body["email"] == users["icc"].institutional_email
    assert body["role"] == "icc"
    assert body["is_active"] is True


def test_me_requires_authentication(client):
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.privacy
def test_me_reflects_the_database_not_the_request(client, users, session):
    """The role a client displays must come from here, not from anything it
    holds locally."""
    response = client.get(
        "/api/v1/auth/me",
        headers={**auth(users["student"]), "X-Role": "admin"},
    )
    assert response.get_json()["role"] == "student"

    users["student"].role = __import__("app.models.enums", fromlist=["UserRole"]).UserRole.SECURITY
    session.flush()

    response = client.get("/api/v1/auth/me", headers=auth(users["student"]))
    assert response.get_json()["role"] == "security"


@pytest.mark.privacy
def test_me_exposes_only_the_callers_own_identity(client, users):
    """`user_id` is returned here because it is the caller's own. It must not
    carry anything about anyone else."""
    body = client.get("/api/v1/auth/me", headers=auth(users["student"])).get_json()

    assert set(body) == {"user_id", "email", "role", "is_active", "display_name"}
    assert body["display_name"] is None
    assert users["other_student"].institutional_email not in str(body)


# ---------------------------------------------------------------------------
# Profile updates — PATCH /auth/me, the one editable field
# ---------------------------------------------------------------------------


def patch_me(client, user, **body):
    return client.patch("/api/v1/auth/me", json=body, headers=auth(user))


def test_a_staff_member_can_set_their_own_display_name(client, users, session):
    response = patch_me(client, users["icc"], display_name="New ICC Name")
    assert response.status_code == 200
    assert response.get_json()["display_name"] == "New ICC Name"

    stored = session.scalar(
        text("SELECT display_name FROM identity.app_user WHERE user_id = :uid"),
        {"uid": str(users["icc"].user_id)},
    )
    assert stored == "New ICC Name"


def test_the_new_name_persists_across_a_later_request(client, users):
    patch_me(client, users["security"], display_name="Front Gate Security")
    response = client.get("/api/v1/auth/me", headers=auth(users["security"]))
    assert response.get_json()["display_name"] == "Front Gate Security"


@pytest.mark.privacy
def test_a_student_cannot_set_a_display_name(client, users, session):
    """The role is refused before any write reaches the database — a clean
    403, not the CHECK constraint's 409."""
    response = patch_me(client, users["student"], display_name="Some Name")
    assert response.status_code == 403

    stored = session.scalar(
        text("SELECT display_name FROM identity.app_user WHERE user_id = :uid"),
        {"uid": str(users["student"].user_id)},
    )
    assert stored is None


def test_update_me_requires_authentication(client):
    response = client.patch("/api/v1/auth/me", json={"display_name": "x"})
    assert response.status_code == 401


def test_a_blank_display_name_is_rejected(client, users):
    response = patch_me(client, users["icc"], display_name="   ")
    assert response.status_code == 400


def test_an_overlong_display_name_is_rejected(client, users):
    response = patch_me(client, users["icc"], display_name="x" * 121)
    assert response.status_code == 400


def test_an_unknown_field_is_rejected(client, users):
    response = patch_me(client, users["icc"], display_name="Fine", role="admin")
    assert response.status_code == 400


def test_leading_and_trailing_whitespace_is_trimmed(client, users):
    response = patch_me(client, users["icc"], display_name="  Padded Name  ")
    assert response.status_code == 200
    assert response.get_json()["display_name"] == "Padded Name"


@pytest.mark.privacy
def test_updating_a_display_name_is_audited(client, users, session):
    patch_me(client, users["admin"], display_name="Named Admin")
    row = session.execute(
        text(
            "SELECT action, object_id FROM audit.access_log "
            "WHERE action = 'account.update_profile' ORDER BY occurred_at DESC LIMIT 1"
        )
    ).one()
    assert row.action == "account.update_profile"
    assert row.object_id == str(users["admin"].user_id)


# ---------------------------------------------------------------------------
# CORS — without it the browser never reaches any of the above
# ---------------------------------------------------------------------------


def test_cors_allows_the_configured_frontend_origin(client):
    response = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"


def test_cors_refuses_an_unlisted_origin(client):
    response = client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
    assert response.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"


def test_preflight_permits_the_authorization_header(client):
    """`Authorization` is not CORS-safelisted, so a preflight that omits it makes
    every authenticated call fail before it is sent."""
    response = client.options(
        "/api/v1/locations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code in {200, 204}
    assert "authorization" in response.headers.get("Access-Control-Allow-Headers", "").lower()
