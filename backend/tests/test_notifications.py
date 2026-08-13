"""In-app notifications: creation from the case lifecycle, and the inbox API.

Real PostgreSQL, real HTTP, matching every other test file here. Two things
this file exists to prove beyond the basic CRUD: a notification is never
created for an anonymous reporter or a self-assignment, and a notification's
title/body never carries anything beyond a fixed template, a status label,
and a public reference.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from .conftest import auth


def create(client, users, report_payload, **overrides):
    response = client.post(
        "/api/v1/reports", json=report_payload(**overrides), headers=auth(users["student"])
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def status(client, user, ref, target, **body):
    return client.post(
        f"/api/v1/incidents/{ref}/status",
        json={"target": target, **body},
        headers=auth(user),
    )


def assign(client, user, ref, **body):
    return client.post(f"/api/v1/incidents/{ref}/assign", json=body, headers=auth(user))


def notifications_for(client, user, **params):
    return client.get("/api/v1/notifications", query_string=params, headers=auth(user))


def mark_read(client, user, notification_id):
    return client.post(f"/api/v1/notifications/{notification_id}/read", headers=auth(user))


def db_notifications(session, user_id):
    return session.execute(
        text(
            "SELECT category::text, title, body, related_report_id, read_at "
            "FROM notify.notification WHERE recipient_user_id = :uid ORDER BY created_at"
        ),
        {"uid": str(user_id)},
    ).all()


# ---------------------------------------------------------------------------
# Creation — status changes
# ---------------------------------------------------------------------------


def test_a_visible_status_change_notifies_the_identified_reporter(
    client, users, session, report_payload
):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=True)

    rows = db_notifications(session, users["student"].user_id)
    assert len(rows) == 1
    assert rows[0].category == "status_update"
    assert ref in rows[0].body


def test_an_internal_only_status_change_notifies_nobody(client, users, session, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=False)

    assert db_notifications(session, users["student"].user_id) == []


@pytest.mark.privacy
def test_an_anonymous_report_never_produces_a_notification(client, users, session, report_payload):
    """No `identity.report_attribution` row means no recipient to write —
    this is a structural guarantee, not a filter that could be bypassed."""
    created = create(client, users, report_payload, anonymous=True)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=True)

    total = session.scalar(text("SELECT count(*) FROM notify.notification"))
    assert total == 0


@pytest.mark.privacy
def test_notification_body_carries_no_narrative_text(client, users, session, report_payload):
    narrative = "A very specific and identifying account of what happened here."
    created = create(client, users, report_payload, narrative=narrative)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=True)

    rows = db_notifications(session, users["student"].user_id)
    assert narrative not in rows[0].body
    assert narrative not in rows[0].title


def test_a_second_visible_transition_adds_a_second_notification(
    client, users, session, report_payload
):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=True)
    assign(client, users["icc"], ref)
    status(client, users["icc"], ref, "under_review", remark="Reviewing.", visible_to_reporter=True)

    rows = db_notifications(session, users["student"].user_id)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# Creation — assignment
# ---------------------------------------------------------------------------


def test_assigning_to_a_colleague_notifies_the_assignee(client, users, session, report_payload):
    from app.models import AppUser
    from app.models.enums import UserRole

    second = AppUser(
        firebase_uid="test-icc-notif",
        role=UserRole.ICC,
        institutional_email="icc-notif@test.local",
        display_name="Second ICC",
    )
    session.add(second)
    session.flush()

    created = create(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref, assignee_user_id=str(second.user_id))

    rows = db_notifications(session, second.user_id)
    assert len(rows) == 1
    assert rows[0].category == "assignment"
    assert ref in rows[0].body


def test_self_assignment_notifies_nobody(client, users, session, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    assert db_notifications(session, users["icc"].user_id) == []


def test_assignment_never_notifies_the_reporter(client, users, session, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    assert db_notifications(session, users["student"].user_id) == []


# ---------------------------------------------------------------------------
# GET /notifications
# ---------------------------------------------------------------------------


def test_listing_notifications_requires_authentication(client):
    assert client.get("/api/v1/notifications").status_code == 401


def test_a_reporter_sees_their_own_notification(client, users, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=True)

    body = notifications_for(client, users["student"]).get_json()
    assert body["unread_count"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["related_public_ref"] == ref
    assert body["items"][0]["read_at"] is None


def test_notifications_are_scoped_to_the_caller(client, users, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)

    body = notifications_for(client, users["other_student"]).get_json()
    assert body["items"] == []
    assert body["unread_count"] == 0


def test_unread_only_filters_out_read_notifications(client, users, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)

    notification_id = notifications_for(client, users["student"]).get_json()["items"][0][
        "notification_id"
    ]
    mark_read(client, users["student"], notification_id)

    body = notifications_for(client, users["student"], unread_only="true").get_json()
    assert body["items"] == []
    assert body["unread_count"] == 0


def test_a_notification_never_carries_a_bare_report_id(client, users, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)

    body = notifications_for(client, users["student"]).get_json()
    assert "related_report_id" not in body["items"][0]


def test_a_notification_never_exposes_delivery_internals(client, users, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)

    body = notifications_for(client, users["student"]).get_json()
    item = body["items"][0]
    assert "delivery_state" not in item
    assert "fcm_message_id" not in item


# ---------------------------------------------------------------------------
# POST /notifications/<id>/read
# ---------------------------------------------------------------------------


def test_marking_a_notification_read_is_idempotent(client, users, session, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)
    notification_id = notifications_for(client, users["student"]).get_json()["items"][0][
        "notification_id"
    ]

    first = mark_read(client, users["student"], notification_id)
    second = mark_read(client, users["student"], notification_id)
    assert first.status_code == 204
    assert second.status_code == 204

    rows = db_notifications(session, users["student"].user_id)
    assert rows[0].read_at is not None


@pytest.mark.privacy
def test_marking_someone_elses_notification_read_is_not_found(client, users, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)
    notification_id = notifications_for(client, users["student"]).get_json()["items"][0][
        "notification_id"
    ]

    response = mark_read(client, users["other_student"], notification_id)
    assert response.status_code == 404


def test_marking_read_requires_authentication(client, users, report_payload):
    created = create(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged", visible_to_reporter=True)
    notification_id = notifications_for(client, users["student"]).get_json()["items"][0][
        "notification_id"
    ]

    response = client.post(f"/api/v1/notifications/{notification_id}/read")
    assert response.status_code == 401


def test_a_malformed_notification_id_is_a_bad_request(client, users):
    response = mark_read(client, users["student"], "not-a-uuid")
    assert response.status_code == 400


def test_marking_a_nonexistent_notification_read_is_not_found(client, users):
    response = mark_read(client, users["student"], "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Status-label completeness
# ---------------------------------------------------------------------------


def test_every_report_status_has_a_notification_label():
    """A status added to the enum without a matching label would otherwise
    fail silently at notification time with a bare KeyError."""
    from app.models.enums import ReportStatus
    from app.services.notification_service import _STATUS_LABELS

    assert set(_STATUS_LABELS) == set(ReportStatus)
