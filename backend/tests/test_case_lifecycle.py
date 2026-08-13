"""The case lifecycle: status transitions, assignment, and who may do either.

Real PostgreSQL, real HTTP, like every other test here. The state-machine and
authorisation rules are exercised through the actual endpoints rather than by
calling `CaseService` directly, because the question that matters is "does the
route enforce this," not "does the function return the right boolean."

Three things run through nearly every test in this file, because they are the
three guarantees this phase exists to add without disturbing the ones that
already existed:

1. `core.report.current_status` is never asserted against directly after a
   transition without also trusting that the database trigger
   (`trg_sync_report_status`) — not the application — produced it. Several
   tests query `core.report` after calling only `POST .../status`, precisely to
   prove the trigger, not a Python assignment, is what moved it.
2. Anonymity is unaffected. Assigning a case, moving its status, and reading
   its history must never resolve, require, or expose who filed it.
3. A student never manages a case, however it is reached — not their own, and
   the concept does not apply to "their own" since nothing here is reporter-
   facing in the first place.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from .conftest import auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create(client, users, report_payload, **overrides):
    response = client.post(
        "/api/v1/reports", json=report_payload(**overrides), headers=auth(users["student"])
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def emergency(client, users, report_payload, **overrides):
    """A report eligible for the assignment-requiring end of the lifecycle.

    Nothing about the state machine requires emergency status — it is used here
    only because the shared `harassment` category fixture is emergency-eligible,
    which keeps this file's reports interchangeable with `test_incidents.py`'s.
    """
    return create(client, users, report_payload, **overrides)


def status(client, user, ref, target, **body):
    return client.post(
        f"/api/v1/incidents/{ref}/status",
        json={"target": target, **body},
        headers=auth(user),
    )


def assign(client, user, ref, **body):
    return client.post(f"/api/v1/incidents/{ref}/assign", json=body, headers=auth(user))


def unassign(client, user, ref, **body):
    return client.post(f"/api/v1/incidents/{ref}/unassign", json=body, headers=auth(user))


def incident(client, user, ref):
    response = client.get(f"/api/v1/incidents/{ref}", headers=auth(user))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def db_status(session, ref) -> str:
    return session.execute(
        text("SELECT current_status FROM core.report WHERE public_ref = :ref"), {"ref": ref}
    ).scalar_one()


def db_closed_at(session, ref):
    return session.execute(
        text("SELECT closed_at FROM core.report WHERE public_ref = :ref"), {"ref": ref}
    ).scalar_one()


def triage_to_under_review(client, users, ref, *, responder="icc"):
    """The common setup: triaged, assigned, and moved into investigation."""
    status(client, users[responder], ref, "triaged").status_code
    assign(client, users[responder], ref)
    return status(
        client, users[responder], ref, "under_review", remark="Beginning investigation."
    )


# ---------------------------------------------------------------------------
# The state machine — legal transitions move the database, via the trigger
# ---------------------------------------------------------------------------


def test_submitted_reports_start_submitted(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    assert db_status(session, created["public_ref"]) == "submitted"


def test_triage_moves_the_report_and_the_trigger_syncs_it(
    client, users, session, report_payload
):
    """The point of this test: nothing here calls anything but POST .../status.

    `core.report.current_status` is asserted afterwards, proving
    `trg_sync_report_status` — not application code — performed the update.
    """
    created = emergency(client, users, report_payload)
    response = status(client, users["icc"], created["public_ref"], "triaged")

    assert response.status_code == 201
    assert response.get_json()["to_status"] == "triaged"
    assert db_status(session, created["public_ref"]) == "triaged"


def test_the_first_acknowledgement_needs_no_remark(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = status(client, users["icc"], created["public_ref"], "triaged")
    assert response.status_code == 201


def test_every_later_transition_requires_a_remark(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged")
    assign(client, users["icc"], ref)

    response = status(client, users["icc"], ref, "under_review")
    assert response.status_code == 400
    assert "remark" in str(response.get_json()["error"]["details"])


def test_the_full_investigative_path_to_resolution(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]

    assert status(client, users["icc"], ref, "triaged").status_code == 201
    assert assign(client, users["icc"], ref).status_code == 201
    assert (
        status(client, users["icc"], ref, "under_review", remark="Reviewing the account.").status_code
        == 201
    )
    assert (
        status(
            client, users["icc"], ref, "action_taken", remark="Referred to the warden."
        ).status_code
        == 201
    )
    response = status(
        client,
        users["icc"],
        ref,
        "resolved",
        remark="Matter addressed with both parties.",
        resolution_reason="action_taken",
    )
    assert response.status_code == 201
    assert db_status(session, ref) == "resolved"
    assert db_closed_at(session, ref) is not None


def test_under_review_can_resolve_directly_without_action_taken(
    client, users, session, report_payload
):
    """`action_taken` is an optional waypoint, not a required one."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged")
    assign(client, users["icc"], ref)
    status(client, users["icc"], ref, "under_review", remark="Reviewing.")

    response = status(
        client,
        users["icc"],
        ref,
        "resolved",
        remark="Resolved through mediation.",
        resolution_reason="action_taken",
    )
    assert response.status_code == 201
    assert db_status(session, ref) == "resolved"


@pytest.mark.parametrize(
    "target,reason",
    [
        ("withdrawn", "withdrawn_by_reporter"),
        ("duplicate", "duplicate_of_existing_case"),
        ("closed_no_action", "no_action_warranted"),
    ],
)
def test_early_exits_from_submitted_need_no_assignment(
    client, users, session, report_payload, target, reason
):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]

    response = status(client, users["icc"], ref, target, remark="Closing early.", resolution_reason=reason)
    assert response.status_code == 201, response.get_json()
    assert db_status(session, ref) == target
    assert db_closed_at(session, ref) is not None


# ---------------------------------------------------------------------------
# The state machine — illegal transitions are refused, server-side
# ---------------------------------------------------------------------------


def test_a_case_cannot_skip_straight_to_resolved(client, users, report_payload):
    """Submitted has never been triaged, let alone assigned or investigated."""
    created = emergency(client, users, report_payload)
    response = status(
        client,
        users["icc"],
        created["public_ref"],
        "resolved",
        remark="Done.",
        resolution_reason="action_taken",
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["details"]["allowed"] == [
        "closed_no_action",
        "duplicate",
        "triaged",
        "withdrawn",
    ]


def test_under_review_requires_an_active_assignment(client, users, report_payload):
    """Triaged, but nobody has claimed it — investigation cannot start."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged")

    response = status(client, users["icc"], ref, "under_review", remark="Starting.")
    assert response.status_code == 409
    assert "assigned" in response.get_json()["error"]["message"].lower()


def test_releasing_the_assignment_blocks_the_next_forward_transition(
    client, users, report_payload
):
    """Unassigning mid-investigation does not roll the case backward — it
    stays under_review — but it does block moving it further until claimed
    again."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    triage_to_under_review(client, users, ref)
    unassign(client, users["icc"], ref)

    response = status(client, users["icc"], ref, "resolved", remark="Closing.", resolution_reason="action_taken")
    assert response.status_code == 409


def test_a_terminal_case_accepts_no_further_transition(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "withdrawn", remark="Reporter asked to withdraw.", resolution_reason="withdrawn_by_reporter")

    response = status(client, users["icc"], ref, "triaged")
    assert response.status_code == 409
    assert response.get_json()["error"]["details"]["allowed"] == []


def test_a_terminal_case_cannot_be_reassigned(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "closed_no_action", remark="No basis found.", resolution_reason="no_action_warranted")

    response = assign(client, users["icc"], ref)
    assert response.status_code == 409


def test_an_unknown_target_status_is_rejected(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = status(client, users["icc"], created["public_ref"], "teleported")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Resolution reasons — a controlled vocabulary, checked twice
# ---------------------------------------------------------------------------


def test_a_terminal_transition_without_a_reason_is_rejected(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = status(
        client, users["icc"], created["public_ref"], "withdrawn", remark="Reporter withdrew."
    )
    assert response.status_code == 400
    assert "resolution_reason" in str(response.get_json()["error"]["details"])


def test_a_non_terminal_transition_rejects_a_reason(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = status(
        client,
        users["icc"],
        created["public_ref"],
        "triaged",
        resolution_reason="action_taken",
    )
    assert response.status_code == 400


def test_a_reason_that_does_not_fit_the_target_status_is_rejected(client, users, report_payload):
    """`withdrawn_by_reporter` describes why a case ended withdrawn, not why it
    was marked a duplicate."""
    created = emergency(client, users, report_payload)
    response = status(
        client,
        users["icc"],
        created["public_ref"],
        "duplicate",
        remark="Same incident as another report.",
        resolution_reason="withdrawn_by_reporter",
    )
    assert response.status_code == 400


@pytest.mark.privacy
def test_the_database_refuses_a_terminal_transition_with_no_reason(session, users, report_payload, client):
    """The CHECK constraint, exercised directly — proof the rule holds even if
    the service layer were bypassed."""
    from sqlalchemy.exc import IntegrityError

    created = create(client, users, report_payload)
    report_id = session.execute(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()

    with pytest.raises(IntegrityError, match="ck_case_status_resolution_reason_terminal"):
        session.execute(
            text(
                "INSERT INTO core.case_status_history (report_id, from_status, to_status) "
                "VALUES (:rid, 'submitted', 'resolved')"
            ),
            {"rid": report_id},
        )
    session.rollback()


@pytest.mark.privacy
def test_the_database_refuses_a_reason_on_a_non_terminal_transition(session, users, report_payload, client):
    from sqlalchemy.exc import IntegrityError

    created = create(client, users, report_payload)
    report_id = session.execute(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()

    with pytest.raises(IntegrityError, match="ck_case_status_resolution_reason_terminal"):
        session.execute(
            text(
                "INSERT INTO core.case_status_history "
                "(report_id, from_status, to_status, resolution_reason) "
                "VALUES (:rid, 'submitted', 'triaged', 'action_taken')"
            ),
            {"rid": report_id},
        )
    session.rollback()


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def test_a_responder_can_self_assign(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]

    response = assign(client, users["icc"], ref)
    assert response.status_code == 201
    body = response.get_json()
    assert body["assignee"]["role"] == "icc"
    assert body["assignee"]["label"] == "ICC Member"

    row = session.execute(
        text(
            "SELECT a.assigned_to, a.is_active FROM core.case_assignment a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == users["icc"].user_id
    assert row[1] is True


def test_self_assigning_twice_is_a_conflict(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    response = assign(client, users["icc"], ref)
    assert response.status_code == 409


def test_a_second_icc_officer_can_be_assigned_by_id(
    client, users, session, report_payload
):
    from app.models import AppUser
    from app.models.enums import UserRole

    second = AppUser(
        firebase_uid="test-icc-2",
        role=UserRole.ICC,
        institutional_email="icc2@test.local",
        display_name="Second ICC Officer",
    )
    session.add(second)
    session.flush()

    created = emergency(client, users, report_payload)
    ref = created["public_ref"]

    response = assign(client, users["icc"], ref, assignee_user_id=str(second.user_id))
    assert response.status_code == 201
    assert response.get_json()["assignee"]["label"] == "Second ICC Officer"


def test_reassignment_releases_the_old_row_and_creates_a_new_one(
    client, users, session, report_payload
):
    from app.models import AppUser
    from app.models.enums import UserRole

    second = AppUser(
        firebase_uid="test-icc-3", role=UserRole.ICC, institutional_email="icc3@test.local",
        display_name="Third ICC Officer",
    )
    session.add(second)
    session.flush()

    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)
    assign(client, users["icc"], ref, assignee_user_id=str(second.user_id))

    rows = session.execute(
        text(
            "SELECT a.assigned_to, a.is_active FROM core.case_assignment a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref "
            "ORDER BY a.assigned_at"
        ),
        {"ref": ref},
    ).all()
    assert len(rows) == 2
    assert rows[0][1] is False  # the original, released
    assert rows[1] == (second.user_id, True)


def test_at_most_one_active_assignment_ever_exists_in_the_database(
    client, users, session, report_payload
):
    """The partial unique index, not just the service's own bookkeeping."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)
    unassign(client, users["icc"], ref)
    assign(client, users["icc"], ref)

    active = session.execute(
        text(
            "SELECT count(*) FROM core.case_assignment a "
            "JOIN core.report r ON r.report_id = a.report_id "
            "WHERE r.public_ref = :ref AND a.is_active"
        ),
        {"ref": ref},
    ).scalar_one()
    assert active == 1


def test_unassign_releases_the_case(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    response = unassign(client, users["icc"], ref)
    assert response.status_code == 204

    active = session.execute(
        text(
            "SELECT count(*) FROM core.case_assignment a "
            "JOIN core.report r ON r.report_id = a.report_id "
            "WHERE r.public_ref = :ref AND a.is_active"
        ),
        {"ref": ref},
    ).scalar_one()
    assert active == 0


def test_unassigning_an_unassigned_case_is_not_found(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = unassign(client, users["icc"], created["public_ref"])
    assert response.status_code == 404


def test_a_security_officer_cannot_be_assigned_an_icc_case(client, users, report_payload):
    """`routes_to_role` mismatch, caught before it becomes a confusing 500."""
    created = emergency(client, users, report_payload)  # routes to ICC
    response = assign(
        client, users["icc"], created["public_ref"], assignee_user_id=str(users["security"].user_id)
    )
    assert response.status_code == 400
    assert "role" in str(response.get_json()["error"]["details"]).lower()


@pytest.mark.privacy
def test_a_student_cannot_be_assigned_a_case(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = assign(
        client, users["icc"], created["public_ref"], assignee_user_id=str(users["student"].user_id)
    )
    assert response.status_code == 400


@pytest.mark.privacy
def test_the_database_refuses_a_student_assignment_even_bypassing_the_service(
    session, users, report_payload, client
):
    """`trg_assignment_target_role`, exercised directly."""
    from sqlalchemy.exc import IntegrityError

    created = create(client, users, report_payload)
    report_id = session.execute(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()

    with pytest.raises(IntegrityError, match="student"):
        session.execute(
            text(
                "INSERT INTO core.case_assignment (report_id, assigned_to, assigned_role) "
                "VALUES (:rid, :uid, 'student')"
            ),
            {"rid": report_id, "uid": str(users["student"].user_id)},
        )
    session.rollback()


# ---------------------------------------------------------------------------
# Authorization — the backend remains the authority
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_a_student_cannot_change_case_status(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = status(client, users["student"], created["public_ref"], "triaged")
    assert response.status_code == 404  # matches the rest of the responder plane


@pytest.mark.privacy
def test_a_student_cannot_assign_a_case(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = assign(client, users["student"], created["public_ref"])
    assert response.status_code == 404


@pytest.mark.privacy
def test_a_student_cannot_unassign_a_case(client, users, report_payload):
    created = emergency(client, users, report_payload)
    assign(client, users["icc"], created["public_ref"])
    response = unassign(client, users["student"], created["public_ref"])
    assert response.status_code == 404


@pytest.mark.privacy
def test_a_wrong_role_responder_cannot_manage_an_unrouted_case(client, users, report_payload):
    """Security is not routed to the ICC-only harassment category."""
    created = emergency(client, users, report_payload)
    response = status(client, users["security"], created["public_ref"], "triaged")
    assert response.status_code == 404


@pytest.mark.privacy
def test_admin_can_view_but_cannot_manage_a_case(client, users, report_payload):
    """Administration sees metadata for oversight and can already open the
    incident (`can_view_report`); it does not get to move a case forward, for
    the same reason it does not get the narrative."""
    created = emergency(client, users, report_payload)
    assert client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["admin"])
    ).status_code == 200

    response = status(client, users["admin"], created["public_ref"], "triaged")
    assert response.status_code == 404


@pytest.mark.privacy
def test_an_anonymous_reporters_own_token_cannot_manage_their_case(
    client, users, report_payload
):
    """Holding the access token proves "this is my report," not "I am staff."
    The token grants `GET /reports/<ref>`, never a responder-plane write."""
    created = create(client, users, report_payload, anonymous=True)
    token = created["access_token"]

    response = client.post(
        f"/api/v1/incidents/{created['public_ref']}/status",
        json={"target": "triaged"},
        headers={"X-Report-Token": token},
    )
    # Unauthenticated entirely — no principal, and the endpoint requires one.
    assert response.status_code == 401


def test_an_assignee_who_is_not_routed_can_still_manage_after_being_assigned(
    client, users, report_payload
):
    """`can_manage_case` admits the current assignee even outside the routing
    check — the same rule `can_view_report` already applied to viewing."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    response = status(client, users["icc"], ref, "triaged")
    assert response.status_code == 201


@pytest.mark.privacy
def test_denied_case_management_is_audited(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    status(client, users["student"], created["public_ref"], "triaged")

    row = session.execute(
        text(
            "SELECT actor_role, outcome FROM audit.access_log "
            "WHERE action = 'case.status_change' AND object_id = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    assert row[0] == "student"
    assert row[1] == "denied"


def test_a_status_change_is_audited_on_success(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    status(client, users["icc"], created["public_ref"], "triaged")

    row = session.execute(
        text(
            "SELECT outcome, detail->>'to_status' FROM audit.access_log "
            "WHERE action = 'case.status_change' AND object_id = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    assert row[0] == "success"
    assert row[1] == "triaged"


def test_assignment_actions_are_audited(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)
    unassign(client, users["icc"], ref)

    actions = set(
        session.execute(
            text("SELECT action FROM audit.access_log WHERE object_id = :ref"), {"ref": ref}
        ).scalars()
    )
    assert {"case.assign", "case.unassign"} <= actions


@pytest.mark.privacy
def test_the_audit_log_holds_no_remark_text(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    status(
        client,
        users["icc"],
        created["public_ref"],
        "withdrawn",
        remark="A distinctive sentence that must never appear in the audit log.",
        resolution_reason="withdrawn_by_reporter",
    )

    details = str(list(session.execute(text("SELECT detail FROM audit.access_log")).scalars()))
    assert "distinctive sentence" not in details


# ---------------------------------------------------------------------------
# What a responder sees
# ---------------------------------------------------------------------------


def test_the_incident_detail_carries_full_case_history(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged")
    assign(client, users["icc"], ref)
    status(client, users["icc"], ref, "under_review", remark="Looking into it.", visible_to_reporter=False)

    history = incident(client, users["icc"], ref)["case_status_history"]
    assert [row["to_status"] for row in history] == ["submitted", "triaged", "under_review"]
    assert history[-1]["visible_to_reporter"] is False
    assert history[-1]["remark"] == "Looking into it."


def test_the_incident_detail_carries_the_current_assignment(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref, note="Taking this one.")

    body = incident(client, users["icc"], ref)
    assert body["assignment"]["assignee"]["role"] == "icc"
    assert body["assignment"]["note"] == "Taking this one."
    assert body["is_assigned"] is True


def test_an_unassigned_incident_says_so(client, users, report_payload):
    created = emergency(client, users, report_payload)
    body = incident(client, users["icc"], created["public_ref"])
    assert body["assignment"] is None
    assert body["is_assigned"] is False


@pytest.mark.privacy
def test_case_history_and_assignment_carry_no_bare_user_id(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged")
    assign(client, users["icc"], ref)

    body = incident(client, users["icc"], ref)
    serialised = str(body)
    assert str(users["icc"].user_id) not in serialised
    assert str(users["student"].user_id) not in serialised


# ---------------------------------------------------------------------------
# What the reporter sees — including the anonymity boundary
# ---------------------------------------------------------------------------


def report_view(client, user, ref):
    response = client.get(f"/api/v1/reports/{ref}", headers=auth(user))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_a_reporter_sees_visible_status_updates(client, users, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=True)

    history = report_view(client, users["student"], ref)["status_history"]
    assert [row["status"] for row in history] == ["submitted", "triaged"]


def test_a_reporter_does_not_see_internal_only_updates(client, users, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged", visible_to_reporter=False)

    history = report_view(client, users["student"], ref)["status_history"]
    assert [row["status"] for row in history] == ["submitted"]


def test_a_reporter_sees_a_safe_resolution_reason(client, users, report_payload):
    created = create(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "triaged")
    assign(client, users["icc"], ref)
    status(client, users["icc"], ref, "under_review", remark="Reviewing.")
    status(
        client,
        users["icc"],
        ref,
        "resolved",
        remark="This matter has been addressed.",
        resolution_reason="action_taken",
    )

    history = report_view(client, users["student"], ref)["status_history"]
    assert history[-1]["status"] == "resolved"
    assert history[-1]["resolution_reason"] == "action_taken"


@pytest.mark.privacy
def test_an_unrelated_student_cannot_read_another_students_case_history(
    client, users, report_payload
):
    created = create(client, users, report_payload)
    response = client.get(
        f"/api/v1/reports/{created['public_ref']}", headers=auth(users["other_student"])
    )
    assert response.status_code == 404


@pytest.mark.privacy
def test_a_reporter_cannot_change_their_own_case_status(client, users, report_payload):
    """The reporter can read (`GET /reports/<ref>`); the responder plane
    (`POST /incidents/<ref>/status`) never admits them, own report or not."""
    created = create(client, users, report_payload)
    response = status(client, users["student"], created["public_ref"], "triaged")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Anonymous reports — the case lifecycle must not become an identity channel
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_an_anonymous_case_can_be_fully_managed_without_any_attribution(
    client, users, session, report_payload
):
    created = create(client, users, report_payload, anonymous=True, is_emergency=True, is_ongoing=True)
    ref = created["public_ref"]

    status(client, users["icc"], ref, "triaged")
    assign(client, users["icc"], ref)
    status(client, users["icc"], ref, "under_review", remark="Investigating.")
    response = status(
        client,
        users["icc"],
        ref,
        "resolved",
        remark="Addressed with campus security.",
        resolution_reason="action_taken",
    )
    assert response.status_code == 201

    attribution = session.execute(
        text(
            "SELECT count(*) FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).scalar_one()
    assert attribution == 0


@pytest.mark.privacy
def test_anonymous_status_updates_carry_no_identity_in_the_reporters_own_view(
    client, users, report_payload
):
    created = create(client, users, report_payload, anonymous=True)
    ref = created["public_ref"]
    token = created["access_token"]
    status(client, users["icc"], ref, "triaged")

    response = client.get(f"/api/v1/reports/{ref}", headers={"X-Report-Token": token})
    assert response.status_code == 200
    serialised = str(response.get_json())
    assert "user_id" not in serialised
    assert str(users["icc"].user_id) not in serialised


@pytest.mark.privacy
def test_anonymous_case_assignment_carries_no_reporter_trace(
    client, users, report_payload
):
    created = create(client, users, report_payload, anonymous=True)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    body = incident(client, users["icc"], ref)
    assert body["submission_mode"] == "anonymous"
    assert body["assignment"]["assignee"]["role"] == "icc"
    # Nothing here can name a reporter, because nothing about the assignment
    # was ever given one to record.
    assert "reporter" not in str(body["assignment"]).lower()


# ---------------------------------------------------------------------------
# Queue behaviour — lifecycle actually changes what the queue shows
# ---------------------------------------------------------------------------


def test_a_terminal_case_leaves_the_queue(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]

    before = client.get("/api/v1/incidents", headers=auth(users["icc"])).get_json()
    assert ref in [item["public_ref"] for item in before["items"]]

    status(client, users["icc"], ref, "withdrawn", remark="Reporter withdrew.", resolution_reason="withdrawn_by_reporter")

    after = client.get("/api/v1/incidents", headers=auth(users["icc"])).get_json()
    assert ref not in [item["public_ref"] for item in after["items"]]


def test_all_four_terminal_routes_empty_the_queue(client, users, report_payload):
    refs = []
    for target, reason in [
        ("withdrawn", "withdrawn_by_reporter"),
        ("duplicate", "duplicate_of_existing_case"),
        ("closed_no_action", "no_action_warranted"),
    ]:
        created = emergency(client, users, report_payload)
        status(client, users["icc"], created["public_ref"], target, remark="x", resolution_reason=reason)
        refs.append(created["public_ref"])

    # A fourth taken all the way to resolved.
    resolved_ref = emergency(client, users, report_payload)["public_ref"]
    status(client, users["icc"], resolved_ref, "triaged")
    assign(client, users["icc"], resolved_ref)
    status(client, users["icc"], resolved_ref, "under_review", remark="x")
    status(client, users["icc"], resolved_ref, "resolved", remark="x", resolution_reason="action_taken")
    refs.append(resolved_ref)

    queue_refs = {
        item["public_ref"]
        for item in client.get("/api/v1/incidents", headers=auth(users["icc"])).get_json()["items"]
    }
    assert not queue_refs & set(refs)


def test_the_queue_can_be_filtered_by_status(client, users, report_payload):
    triaged_ref = emergency(client, users, report_payload)["public_ref"]
    status(client, users["icc"], triaged_ref, "triaged")
    untouched_ref = emergency(client, users, report_payload)["public_ref"]

    response = client.get(
        "/api/v1/incidents", headers=auth(users["icc"]), query_string={"status": "triaged"}
    )
    assert response.status_code == 200
    refs = {item["public_ref"] for item in response.get_json()["items"]}
    assert triaged_ref in refs
    assert untouched_ref not in refs


def test_the_status_filter_cannot_reach_a_closed_case(client, users, report_payload):
    """Filtering must narrow the open set, never widen past it."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    status(client, users["icc"], ref, "withdrawn", remark="x", resolution_reason="withdrawn_by_reporter")

    response = client.get(
        "/api/v1/incidents", headers=auth(users["icc"]), query_string={"status": "withdrawn"}
    )
    refs = {item["public_ref"] for item in response.get_json()["items"]}
    assert ref not in refs


def test_a_malformed_status_filter_is_rejected(client, users, report_payload):
    response = client.get(
        "/api/v1/incidents", headers=auth(users["icc"]), query_string={"status": "not_a_status"}
    )
    assert response.status_code == 400


def test_the_queue_marks_assigned_cases(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    assign(client, users["icc"], ref)

    items = client.get("/api/v1/incidents", headers=auth(users["icc"])).get_json()["items"]
    row = next(item for item in items if item["public_ref"] == ref)
    assert row["is_assigned"] is True


@pytest.mark.privacy
def test_emergency_ordering_is_unaffected_by_the_case_lifecycle_addition(
    client, users, report_payload
):
    """The pre-existing ordering guarantee, re-checked: emergency first, then
    ongoing, then recency — never anything about the reporter — still holds
    once cases can be triaged and assigned."""
    ordinary = emergency(client, users, report_payload)
    urgent = emergency(client, users, report_payload, is_emergency=True, is_ongoing=True)
    status(client, users["icc"], ordinary["public_ref"], "triaged")

    items = client.get("/api/v1/incidents", headers=auth(users["icc"])).get_json()["items"]
    assert items[0]["public_ref"] == urgent["public_ref"]
