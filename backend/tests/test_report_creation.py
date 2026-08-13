"""Report creation: identified, anonymous, emergency, and validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from .conftest import auth, staged_token


def _create(client, user, payload):
    return client.post("/api/v1/reports", json=payload, headers=auth(user))


# ---------------------------------------------------------------------------
# Identified reports
# ---------------------------------------------------------------------------


def test_identified_report_is_created(client, users, report_payload):
    response = _create(client, users["student"], report_payload())
    assert response.status_code == 201

    body = response.get_json()
    assert body["submission_mode"] == "identified"
    assert body["report_kind"] == "incident"
    assert body["status"] == "submitted"
    assert body["public_ref"].startswith("CS-")
    # No token: an identified reporter finds the report through their account.
    assert "access_token" not in body


def test_identified_report_creates_attribution(client, users, session, report_payload):
    ref = _create(client, users["student"], report_payload()).get_json()["public_ref"]

    attributed = session.scalar(
        text(
            "SELECT a.user_id FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert attributed == users["student"].user_id


def test_identified_report_is_contactable_with_consent(client, users, session, report_payload):
    """`reporter_contactable` is derived from consent by a database trigger, not
    set by the application."""
    ref = _create(client, users["student"], report_payload()).get_json()["public_ref"]
    contactable = session.scalar(
        text("SELECT reporter_contactable FROM core.report WHERE public_ref = :ref"),
        {"ref": ref},
    )
    assert contactable is True


def test_consent_withheld_leaves_reporter_uncontactable(client, users, session, report_payload):
    ref = _create(client, users["student"], report_payload(contact_consent=False)).get_json()[
        "public_ref"
    ]
    contactable = session.scalar(
        text("SELECT reporter_contactable FROM core.report WHERE public_ref = :ref"),
        {"ref": ref},
    )
    assert contactable is False


def test_concern_report_takes_kind_from_category(client, users, categories, report_payload):
    body = _create(
        client, users["student"], report_payload(category_id=categories["lighting"].category_id)
    ).get_json()
    assert body["report_kind"] == "concern"


def test_narrative_is_stored_with_retention_stamped(client, users, session, report_payload):
    """Retention days come from `core.system_policy` via trigger; the application
    never supplies them."""
    ref = _create(client, users["student"], report_payload()).get_json()["public_ref"]
    row = session.execute(
        text(
            "SELECT n.narrative_retention_days_applied, n.redacted_retention_days_applied "
            "FROM core.report_narrative n JOIN core.report r ON r.report_id = n.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == 365
    assert row[1] == 730


def test_evidence_reference_is_attached(client, users, session, report_payload):
    """An uploaded image becomes an evidence row on the report.

    The payload carries a capability token, not a storage path. A client can no
    longer describe an evidence object into existence: the row is built from what
    the server itself recorded when it processed the bytes.
    """
    token = staged_token(client, users["student"])
    payload = report_payload(evidence_tokens=[token])
    ref = _create(client, users["student"], payload).get_json()["public_ref"]

    row = session.execute(
        text(
            "SELECT e.storage_path, e.content_type, e.byte_size, e.image_width "
            "FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    storage_path, content_type, byte_size, width = row
    assert content_type == "image/jpeg"
    assert byte_size > 0
    assert width == 320
    # Server-generated and opaque: no filename, no user, no report reference.
    assert storage_path.startswith("evidence/")
    assert "photo" not in storage_path
    assert ref not in storage_path


@pytest.mark.privacy
def test_evidence_never_records_the_original_filename(client, users, session, report_payload):
    """No report keeps the name of the file the student chose.

    This is a change of contract in 4B-1. Previously the filename was retained
    for identified reports and dropped only for anonymous ones. It is now dropped
    for both, because the upload endpoint cannot know which kind of report the
    image will end up on — the token is issued hours before the report exists,
    and the staging row deliberately has no user column. Keeping the name until
    attachment time in order to decide would mean holding
    `IMG_20260810_Priya_hostel.jpg` in a table that the anonymity design says
    must contain nothing identifying.
    """
    token = staged_token(client, users["student"], filename="IMG_20260810_Priya_hostel.jpg")
    ref = _create(client, users["student"], report_payload(evidence_tokens=[token])).get_json()[
        "public_ref"
    ]

    stored = session.execute(
        text(
            "SELECT e.original_filename FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).scalar_one()
    assert stored is None


# ---------------------------------------------------------------------------
# Anonymous reports
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_anonymous_report_returns_one_time_token(client, users, report_payload):
    response = _create(client, users["student"], report_payload(anonymous=True))
    assert response.status_code == 201

    body = response.get_json()
    assert body["submission_mode"] == "anonymous"
    assert len(body["access_token"]) == 32  # 128 bits, hex
    assert "shown once" in body["access_token_notice"]


@pytest.mark.privacy
def test_anonymous_report_creates_no_attribution(client, users, session, report_payload):
    """The guarantee. Anonymity is the absence of this row, and a database
    trigger refuses to create one even if the service tried."""
    ref = _create(client, users["student"], report_payload(anonymous=True)).get_json()["public_ref"]

    count = session.scalar(
        text(
            "SELECT count(*) FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert count == 0


@pytest.mark.privacy
def test_database_refuses_attribution_for_anonymous_report(client, users, session, report_payload):
    """Defence in depth: bypass the service entirely and go at the table."""
    ref = _create(client, users["student"], report_payload(anonymous=True)).get_json()["public_ref"]
    report_id = session.scalar(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"), {"ref": ref}
    )

    with pytest.raises(Exception) as excinfo:
        session.execute(
            text(
                "INSERT INTO identity.report_attribution (report_id, user_id) VALUES (:rid, :uid)"
            ),
            {"rid": str(report_id), "uid": str(users["student"].user_id)},
        )
    assert "anonymity violation" in str(excinfo.value).lower()
    session.rollback()


@pytest.mark.privacy
def test_anonymous_report_is_never_contactable(client, users, session, report_payload):
    ref = _create(
        client, users["student"], report_payload(anonymous=True, contact_consent=True)
    ).get_json()["public_ref"]

    contactable = session.scalar(
        text("SELECT reporter_contactable FROM core.report WHERE public_ref = :ref"),
        {"ref": ref},
    )
    assert contactable is False


@pytest.mark.privacy
def test_anonymous_evidence_drops_original_filename(client, users, session, report_payload):
    """`IMG_20260810_Priya_hostel.jpg` names a person, a place and a date."""
    token = staged_token(client, users["student"], filename="IMG_20260810_Priya_hostel.jpg")
    payload = report_payload(anonymous=True, evidence_tokens=[token])
    ref = _create(client, users["student"], payload).get_json()["public_ref"]

    stored = session.execute(
        text(
            "SELECT e.original_filename FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).scalar_one()
    assert stored is None


@pytest.mark.privacy
def test_anonymous_report_absent_from_my_reports(client, users, report_payload):
    """Not a gap: nothing starting from a user id can reach an anonymous report,
    including this endpoint."""
    anon_ref = _create(client, users["student"], report_payload(anonymous=True)).get_json()[
        "public_ref"
    ]
    named_ref = _create(client, users["student"], report_payload()).get_json()["public_ref"]

    listed = client.get("/api/v1/reports/mine", headers=auth(users["student"])).get_json()
    refs = {item["public_ref"] for item in listed["items"]}

    assert named_ref in refs
    assert anon_ref not in refs


# ---------------------------------------------------------------------------
# Emergency reports
# ---------------------------------------------------------------------------


def test_emergency_anonymous_report_is_accepted(client, users, session, report_payload):
    """The decision-1 requirement: an anonymous emergency is permitted, and
    dispatch gets the location while the reporter stays unreachable."""
    response = _create(
        client,
        users["student"],
        report_payload(anonymous=True, is_emergency=True, is_ongoing=True),
    )
    assert response.status_code == 201

    body = response.get_json()
    assert body["is_emergency"] is True
    assert body["is_ongoing"] is True
    assert body["reporter_contactable"] is False
    assert body["location"]["code"] == "TEST-ACTIVE"
    assert body["access_token"]


def test_emergency_identified_report_is_contactable(client, users, report_payload):
    body = _create(client, users["student"], report_payload(is_emergency=True)).get_json()
    assert body["is_emergency"] is True
    assert body["reporter_contactable"] is True


def test_emergency_rejected_for_non_eligible_category(client, users, categories, report_payload):
    """A broken streetlight is not an emergency."""
    response = _create(
        client,
        users["student"],
        report_payload(category_id=categories["lighting"].category_id, is_emergency=True),
    )
    assert response.status_code == 400
    assert "emergency" in response.get_json()["error"]["message"].lower()


def test_is_ongoing_requires_emergency(client, users, report_payload):
    response = _create(client, users["student"], report_payload(is_ongoing=True))
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_invalid_category_is_rejected(client, users, report_payload):
    response = _create(client, users["student"], report_payload(category_id=999999))
    assert response.status_code == 400
    body = response.get_json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert "category_id" in body["details"]["fields"]


def test_inactive_category_is_rejected(client, users, categories, report_payload):
    response = _create(
        client, users["student"], report_payload(category_id=categories["inactive"].category_id)
    )
    assert response.status_code == 400
    assert "category_id" in response.get_json()["error"]["details"]["fields"]


def test_invalid_location_is_rejected(client, users, report_payload):
    response = _create(client, users["student"], report_payload(location_id=999999))
    assert response.status_code == 400
    body = response.get_json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert "location_id" in body["details"]["fields"]


def test_unsurveyed_location_is_rejected(client, users, locations, report_payload):
    """An un-surveyed location has no coordinates; a report against it could
    never reach a map."""
    response = _create(
        client, users["student"], report_payload(location_id=locations["unsurveyed"].location_id)
    )
    assert response.status_code == 400
    assert "location_id" in response.get_json()["error"]["details"]["fields"]


def test_invalid_reporter_relationship_is_rejected(client, users, report_payload):
    response = _create(client, users["student"], report_payload(reporter_relationship="victim"))
    assert response.status_code == 400
    body = response.get_json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert "reporter_relationship" in body["details"]["fields"]


@pytest.mark.parametrize("relationship", ["affected", "witness", "third_party"])
def test_valid_reporter_relationships_are_accepted(client, users, report_payload, relationship):
    response = _create(client, users["student"], report_payload(reporter_relationship=relationship))
    assert response.status_code == 201
    assert response.get_json()["reporter_relationship"] == relationship


def test_reporter_relationship_defaults_to_affected(client, users, report_payload):
    """The default matters: a reporter who skips the question is never sorted
    into a lesser tier."""
    body = _create(client, users["student"], report_payload()).get_json()
    assert body["reporter_relationship"] == "affected"


def test_missing_required_field_is_rejected(client, users, report_payload):
    payload = report_payload()
    del payload["narrative"]
    response = _create(client, users["student"], payload)
    assert response.status_code == 400
    assert "narrative" in response.get_json()["error"]["details"]["fields"]


def test_short_narrative_is_rejected(client, users, report_payload):
    response = _create(client, users["student"], report_payload(narrative="too short"))
    assert response.status_code == 400


def test_future_occurrence_is_rejected(client, users, report_payload):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = _create(client, users["student"], report_payload(occurred_at=future))
    assert response.status_code == 400
    assert "future" in response.get_json()["error"]["message"].lower()


def test_unknown_field_is_rejected(client, users, report_payload):
    response = _create(client, users["student"], report_payload(sneaky_field="value"))
    assert response.status_code == 400


@pytest.mark.privacy
def test_client_cannot_set_submission_mode_directly(client, users, report_payload):
    """`submission_mode` is immutable once set; `anonymous` is the supported
    input, and the two must not become separate levers."""
    response = _create(client, users["student"], report_payload(submission_mode="anonymous"))
    assert response.status_code == 400
    assert "submission_mode" in response.get_json()["error"]["details"]["fields"]


@pytest.mark.privacy
def test_client_cannot_set_reporter_identity(client, users, report_payload):
    response = _create(
        client, users["student"], report_payload(user_id=str(users["other_student"].user_id))
    )
    assert response.status_code == 400
    assert "user_id" in response.get_json()["error"]["details"]["fields"]


@pytest.mark.privacy
def test_client_cannot_force_contactability(client, users, report_payload):
    response = _create(client, users["student"], report_payload(reporter_contactable=True))
    assert response.status_code == 400
    assert "reporter_contactable" in response.get_json()["error"]["details"]["fields"]


@pytest.mark.privacy
def test_client_cannot_submit_a_raw_coordinate_as_the_incident_location(
    client, users, report_payload
):
    """The incident anchors to `location_id`, the controlled vocabulary —
    never a client-supplied latitude/longitude. Rejected explicitly, with a
    stated reason, not merely as an unrecognised field.

    The coordinate below is arbitrary and synthetic, chosen only to prove
    the field is rejected outright — this project never writes a
    real-looking campus coordinate anywhere, including as a negative
    example in a test.
    """
    response = _create(client, users["student"], report_payload(latitude=12.5, longitude=77.5))
    assert response.status_code == 400
    fields = response.get_json()["error"]["details"]["fields"]
    assert "latitude" in fields
    assert "longitude" in fields


def test_malformed_json_is_rejected(client, users):
    response = client.post(
        "/api/v1/reports",
        data="not json",
        content_type="application/json",
        headers=auth(users["student"]),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] in {"MALFORMED_REQUEST", "VALIDATION_ERROR"}


def test_wrong_content_type_is_rejected(client, users):
    response = client.post(
        "/api/v1/reports", data="a=b", content_type="text/plain", headers=auth(users["student"])
    )
    assert response.status_code in {400, 415}


def test_creation_requires_authentication(client, report_payload):
    response = client.post("/api/v1/reports", json=report_payload())
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "UNAUTHENTICATED"


def test_unknown_dev_user_is_rejected(client, report_payload):
    response = client.post(
        "/api/v1/reports",
        json=report_payload(),
        headers={"X-Dev-User": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 401


def test_report_creation_is_audited(client, users, session, report_payload):
    ref = _create(client, users["student"], report_payload()).get_json()["public_ref"]
    logged = session.scalar(
        text(
            "SELECT count(*) FROM audit.access_log "
            "WHERE action = 'report.create' AND object_id = :ref"
        ),
        {"ref": ref},
    )
    assert logged == 1


def test_error_response_carries_a_request_id(client, users, report_payload):
    response = _create(client, users["student"], report_payload(category_id=999999))
    body = response.get_json()["error"]
    assert body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]
