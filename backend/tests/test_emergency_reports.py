"""Emergency ("SOS") reports: creation, location handling, dispatch reuse,
duplicate-press handling, quota bypass, evidence-after-creation, and the
privacy/anonymity guarantees the normal flow already relies on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .conftest import auth, staged_token


def _sos(client, user, **body):
    return client.post("/api/v1/reports/emergency", json=body, headers=auth(user))


def dispatch_url(ref: str) -> str:
    return f"/api/v1/incidents/{ref}/dispatch"


# ---------------------------------------------------------------------------
# Minimal creation
# ---------------------------------------------------------------------------


def test_sos_with_no_body_succeeds(client, users):
    """The whole point: zero fields required."""
    response = _sos(client, users["student"])
    assert response.status_code == 201

    body = response.get_json()
    assert body["is_emergency"] is True
    assert body["is_ongoing"] is False
    assert body["submission_mode"] == "identified"
    assert body["reporter_contactable"] is True
    assert body["category"]["code"] == "SOS_EMERGENCY"
    assert body["public_ref"].startswith("CS-")
    assert "access_token" not in body


def test_sos_narrative_is_a_system_placeholder(client, users, session):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    narrative = session.scalar(
        text(
            "SELECT n.narrative FROM core.report_narrative n "
            "JOIN core.report r ON r.report_id = n.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert "Emergency SOS triggered" in narrative


def test_sos_is_always_identified_even_if_never_asked(client, users, session):
    """No `anonymous` field exists on this endpoint's schema at all — the
    person who may need to be reached is exactly who this creates a record
    for."""
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    attributed = session.scalar(
        text(
            "SELECT a.user_id FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert attributed == users["student"].user_id


def test_sos_rejects_an_unknown_field(client, users):
    """`_Strict` still applies: an emergency payload is minimal, not lax."""
    response = _sos(client, users["student"], user_id="anything")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Location handling
# ---------------------------------------------------------------------------


def test_sos_with_no_location_uses_the_sentinel(client, users, session):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    row = session.execute(
        text(
            "SELECT l.code, l.is_active FROM core.report r "
            "JOIN core.campus_location l ON l.location_id = r.location_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == "SYS-UNSPECIFIED"
    assert row[1] is False


def test_sos_with_no_location_records_unresolved_no_signal(client, users, session):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    row = session.execute(
        text(
            "SELECT resolution, source, signal_latitude FROM core.report_location_detail d "
            "JOIN core.report r ON r.report_id = d.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == "unresolved"
    assert row[1] == "location_default"
    assert row[2] is None


def test_sos_near_a_verified_location_is_anchored_to_it(client, users, session, locations):
    """`locations['active']` is verified at (0.0, 0.0). A device position 11m
    away should match — well inside the emergency search radius."""
    ref = _sos(client, users["student"], latitude=0.0001, longitude=0.0001).get_json()[
        "public_ref"
    ]
    row = session.execute(
        text(
            "SELECT l.code FROM core.report r "
            "JOIN core.campus_location l ON l.location_id = r.location_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == "TEST-ACTIVE"


def test_sos_near_a_verified_location_records_a_device_gps_signal(
    client, users, session, locations
):
    ref = _sos(client, users["student"], latitude=0.0001, longitude=0.0001).get_json()[
        "public_ref"
    ]
    row = session.execute(
        text(
            "SELECT resolution, source FROM core.report_location_detail d "
            "JOIN core.report r ON r.report_id = d.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == "corroborated"
    assert row[1] == "device_gps"


def test_sos_far_from_anything_verified_falls_back_to_the_sentinel(client, users, session):
    """Nowhere near either fixture location (0,0)/(1,1) — nothing surveyed is
    within the match radius, so this still must not fail."""
    response = _sos(client, users["student"], latitude=45.0, longitude=45.0)
    assert response.status_code == 201

    ref = response.get_json()["public_ref"]
    location, resolution = session.execute(
        text(
            "SELECT l.code, d.resolution FROM core.report r "
            "JOIN core.campus_location l ON l.location_id = r.location_id "
            "JOIN core.report_location_detail d ON d.report_id = r.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert location == "SYS-UNSPECIFIED"
    assert resolution == "unresolved"


def test_sos_requires_both_coordinates_or_neither(client, users):
    response = _sos(client, users["student"], latitude=1.0)
    assert response.status_code == 400


def test_sos_rejects_an_out_of_range_coordinate(client, users):
    response = _sos(client, users["student"], latitude=999.0, longitude=1.0)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Duplicate-press handling
# ---------------------------------------------------------------------------


def test_a_second_sos_moments_later_returns_the_same_report(client, users):
    first = _sos(client, users["student"]).get_json()
    second = _sos(client, users["student"]).get_json()
    assert second["public_ref"] == first["public_ref"]


def test_a_second_sos_after_the_dedup_window_creates_a_new_report(client, users, session):
    first_ref = _sos(client, users["student"]).get_json()["public_ref"]

    # Push the first report's submitted_at (and occurred_at along with it, to
    # keep ck_report_occurred_not_future satisfied) outside the dedup window
    # rather than sleeping the test suite for it.
    ten_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    session.execute(
        text(
            "UPDATE core.report SET submitted_at = :ts, occurred_at = :ts "
            "WHERE public_ref = :ref"
        ),
        {"ts": ten_minutes_ago, "ref": first_ref},
    )
    session.flush()

    second_ref = _sos(client, users["student"]).get_json()["public_ref"]
    assert second_ref != first_ref


def test_a_second_sos_from_a_different_reporter_is_not_deduplicated(client, users):
    first = _sos(client, users["student"]).get_json()
    second = _sos(client, users["other_student"]).get_json()
    assert second["public_ref"] != first["public_ref"]


# ---------------------------------------------------------------------------
# Quota bypass
# ---------------------------------------------------------------------------


def test_sos_is_not_blocked_by_an_exhausted_daily_quota(client, users, session, report_payload):
    session.execute(
        text(
            "UPDATE core.system_policy SET policy_value = '1' "
            "WHERE policy_key = 'daily_report_quota'"
        )
    )
    session.flush()

    first = client.post(
        "/api/v1/reports", json=report_payload(), headers=auth(users["student"])
    )
    assert first.status_code == 201

    blocked = client.post(
        "/api/v1/reports", json=report_payload(), headers=auth(users["student"])
    )
    assert blocked.status_code == 429 or blocked.get_json()["error"]["code"] == "QUOTA_EXCEEDED"

    sos = _sos(client, users["student"])
    assert sos.status_code == 201


# ---------------------------------------------------------------------------
# Dispatch lifecycle reuse — no new state machine, the existing one
# ---------------------------------------------------------------------------


def test_a_responder_can_raise_a_dispatch_on_an_sos_report(client, users):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    response = client.post(dispatch_url(ref), headers=auth(users["security"]))
    assert response.status_code == 201
    assert response.get_json()["state"] == "pending"


def test_sos_reports_appear_first_in_the_security_queue(client, users, categories, report_payload):
    """`is_emergency DESC` ordering, exercised end to end rather than assumed."""
    ordinary = client.post(
        "/api/v1/reports",
        json=report_payload(category_id=categories["lighting"].category_id),
        headers=auth(users["student"]),
    ).get_json()
    sos = _sos(client, users["other_student"]).get_json()

    items = client.get("/api/v1/incidents", headers=auth(users["security"])).get_json()["items"]
    refs = [item["public_ref"] for item in items]
    assert refs.index(sos["public_ref"]) < refs.index(ordinary["public_ref"])


# ---------------------------------------------------------------------------
# Evidence attached after creation
# ---------------------------------------------------------------------------


def test_evidence_can_be_attached_to_an_sos_report_afterwards(client, users, session):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    token = staged_token(client, users["student"])

    response = client.post(
        f"/api/v1/reports/{ref}/evidence",
        json={"evidence_tokens": [token]},
        headers=auth(users["student"]),
    )
    assert response.status_code == 201
    assert len(response.get_json()["evidence_ids"]) == 1

    count = session.scalar(
        text(
            "SELECT count(*) FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert count == 1


def test_a_different_reporter_cannot_attach_evidence_to_someone_elses_sos(client, users):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    token = staged_token(client, users["other_student"])

    response = client.post(
        f"/api/v1/reports/{ref}/evidence",
        json={"evidence_tokens": [token]},
        headers=auth(users["other_student"]),
    )
    # 404, not 403 — the same oracle-avoidance reasoning as get_report_detail.
    assert response.status_code == 404


def test_staff_cannot_attach_evidence_even_though_they_can_view_the_report(client, users):
    """`can_attach_evidence` is narrower than `can_view_report` on purpose: a
    responder can see the incident but must not be able to plant evidence on
    someone else's report."""
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    token = staged_token(client, users["student"])

    response = client.post(
        f"/api/v1/reports/{ref}/evidence",
        json={"evidence_tokens": [token]},
        headers=auth(users["security"]),
    )
    assert response.status_code == 404


def test_an_anonymous_access_token_cannot_attach_evidence(client, users, report_payload):
    """A token proves "let me read my own status," not "let me write." """
    created = client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True),
        headers=auth(users["student"]),
    ).get_json()
    token = staged_token(client, users["student"])

    response = client.post(
        f"/api/v1/reports/{created['public_ref']}/evidence",
        json={"evidence_tokens": [token]},
        headers={"X-Report-Token": created["access_token"]},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Authorization / anonymity guarantees hold for the emergency path too
# ---------------------------------------------------------------------------


def test_sos_requires_authentication(client):
    response = client.post("/api/v1/reports/emergency", json={})
    assert response.status_code == 401


def test_a_student_cannot_see_another_students_sos_report(client, users):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    response = client.get(
        f"/api/v1/reports/{ref}", headers=auth(users["other_student"])
    )
    assert response.status_code == 404


def test_sos_report_appears_in_the_reporters_own_report_list(client, users):
    ref = _sos(client, users["student"]).get_json()["public_ref"]
    listed = client.get("/api/v1/reports/mine", headers=auth(users["student"])).get_json()
    refs = {item["public_ref"] for item in listed["items"]}
    assert ref in refs
