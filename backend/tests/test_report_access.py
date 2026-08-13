"""Who may retrieve a report — the authorisation boundary."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from .conftest import auth, staged_token


def _create(client, user, payload):
    return client.post("/api/v1/reports", json=payload, headers=auth(user)).get_json()


# ---------------------------------------------------------------------------
# The reporter
# ---------------------------------------------------------------------------


def test_reporter_can_read_own_report(client, users, report_payload):
    ref = _create(client, users["student"], report_payload())["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["student"]))
    assert response.status_code == 200

    body = response.get_json()
    assert body["public_ref"] == ref
    assert body["narrative_available"] is True
    assert body["narrative"].startswith("A test account")


def test_my_reports_lists_only_own_reports(client, users, report_payload):
    mine = _create(client, users["student"], report_payload())["public_ref"]
    theirs = _create(client, users["other_student"], report_payload())["public_ref"]

    body = client.get("/api/v1/reports/mine", headers=auth(users["student"])).get_json()
    refs = {item["public_ref"] for item in body["items"]}

    assert mine in refs
    assert theirs not in refs
    assert body["pagination"]["total"] == 1


def test_my_reports_paginates(client, users, report_payload):
    for _ in range(3):
        _create(client, users["student"], report_payload())

    body = client.get(
        "/api/v1/reports/mine?limit=2&offset=0", headers=auth(users["student"])
    ).get_json()
    assert body["pagination"]["returned"] == 2
    assert body["pagination"]["total"] == 3


def test_my_reports_requires_authentication(client):
    assert client.get("/api/v1/reports/mine").status_code == 401


# ---------------------------------------------------------------------------
# Cross-student isolation
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_student_cannot_read_another_students_report(client, users, report_payload):
    """Returns 404, not 403.

    A 403 would confirm the reference is real. References are printed on
    acknowledgements and appear in screenshots, so the endpoint must not become
    an oracle for whether a given code exists.
    """
    ref = _create(client, users["other_student"], report_payload())["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["student"]))
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.privacy
def test_unknown_reference_is_indistinguishable_from_forbidden(client, users, report_payload):
    """The two cases must be byte-identical apart from the request id."""
    other_ref = _create(client, users["other_student"], report_payload())["public_ref"]

    forbidden = client.get(f"/api/v1/reports/{other_ref}", headers=auth(users["student"]))
    missing = client.get("/api/v1/reports/CS-2026-ZZZZZZ", headers=auth(users["student"]))

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.get_json()["error"]["message"] == missing.get_json()["error"]["message"]
    assert forbidden.get_json()["error"]["code"] == missing.get_json()["error"]["code"]


@pytest.mark.privacy
def test_anonymous_report_is_not_readable_by_its_submitter_without_token(
    client, users, report_payload
):
    """Even the account that filed it cannot reach it by identity.

    There is no link to follow — which is exactly what makes the anonymity real
    rather than a UI convention.
    """
    ref = _create(client, users["student"], report_payload(anonymous=True))["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["student"]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Token access
# ---------------------------------------------------------------------------


def test_anonymous_reporter_can_read_with_token(client, users, report_payload):
    created = _create(client, users["student"], report_payload(anonymous=True))

    response = client.get(
        f"/api/v1/reports/{created['public_ref']}",
        headers={"X-Report-Token": created["access_token"]},
    )
    assert response.status_code == 200

    body = response.get_json()
    assert body["submission_mode"] == "anonymous"
    assert body["narrative_available"] is True
    assert body["status_history"]


def test_wrong_token_is_refused(client, users, report_payload):
    created = _create(client, users["student"], report_payload(anonymous=True))

    response = client.get(
        f"/api/v1/reports/{created['public_ref']}",
        headers={"X-Report-Token": "f" * 32},
    )
    assert response.status_code == 404


def test_token_for_one_report_does_not_open_another(client, users, report_payload):
    first = _create(client, users["student"], report_payload(anonymous=True))
    second = _create(client, users["student"], report_payload(anonymous=True))

    response = client.get(
        f"/api/v1/reports/{second['public_ref']}",
        headers={"X-Report-Token": first["access_token"]},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Authority access — not "everyone sees everything"
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_icc_sees_reports_routed_to_icc(client, users, report_payload):
    ref = _create(client, users["student"], report_payload())["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["icc"]))
    assert response.status_code == 200
    assert response.get_json()["narrative_available"] is True


@pytest.mark.privacy
def test_security_cannot_see_a_report_routed_to_icc(client, users, report_payload):
    """A role is not access. Security sees reports routed to security."""
    ref = _create(client, users["student"], report_payload())["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["security"]))
    assert response.status_code == 404


@pytest.mark.privacy
def test_security_sees_reports_routed_to_security(client, users, categories, report_payload):
    ref = _create(
        client, users["student"], report_payload(category_id=categories["lighting"].category_id)
    )["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["security"]))
    assert response.status_code == 200


@pytest.mark.privacy
def test_admin_sees_metadata_but_never_the_narrative(client, users, report_payload):
    """The narrative firewall, at the API layer.

    Mirrors the database grants where `cs_analytics` is denied SELECT on
    `core.report_narrative`: Administration gets the pattern layer without
    reading a student's account of what happened to them.
    """
    ref = _create(client, users["student"], report_payload())["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users["admin"]))
    assert response.status_code == 200

    body = response.get_json()
    assert body["public_ref"] == ref
    assert body["status"] == "submitted"
    assert body["narrative"] is None
    assert body["narrative_available"] is False
    assert body["narrative_withheld_reason"] == "not_authorised"


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


@pytest.mark.privacy
@pytest.mark.parametrize("actor", ["student", "icc", "admin"])
def test_no_response_ever_carries_identity(client, users, report_payload, actor):
    """No `user_id` in any shape, for any role.

    Serializers name every field explicitly, so adding a model column cannot
    widen a response by accident — which is precisely how identity leaks.
    """
    ref = _create(client, users["student"], report_payload())["public_ref"]

    response = client.get(f"/api/v1/reports/{ref}", headers=auth(users[actor]))
    if response.status_code != 200:
        pytest.skip(f"{actor} has no access to this report")

    serialised = str(response.get_json())
    assert "user_id" not in serialised
    assert str(users["student"].user_id) not in serialised
    assert "student1@test.local" not in serialised


@pytest.mark.privacy
def test_response_never_exposes_storage_paths(client, users, session, report_payload):
    """`storage_path` is an internal bucket location. Bytes are served by
    streaming them through an authorisation check, never by handing out a
    location the browser can fetch directly."""
    token = staged_token(client, users["student"])
    ref = _create(client, users["student"], report_payload(evidence_tokens=[token]))["public_ref"]

    stored_path = session.execute(
        text(
            "SELECT e.storage_path FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).scalar_one()

    body = client.get(f"/api/v1/reports/{ref}", headers=auth(users["student"])).get_json()
    assert stored_path not in str(body)
    assert "evidence/" not in str(body)
    assert body["evidence_count"] == 1


def test_report_view_is_audited(client, users, session, report_payload):
    ref = _create(client, users["student"], report_payload())["public_ref"]
    client.get(f"/api/v1/reports/{ref}", headers=auth(users["icc"]))

    logged = session.scalar(
        text(
            "SELECT count(*) FROM audit.access_log "
            "WHERE action = 'report.view' AND object_id = :ref"
        ),
        {"ref": ref},
    )
    assert logged >= 1
