"""Responder endpoints — the incident queue, incident detail, and dispatch.

Thin, like every other route module: parse, delegate, audit, commit. The
authorisation decision is `IncidentService`'s; this file asks and reports.

Every read here is audited. A responder queue is a list of other people's worst
days, and who opened which incident is exactly the kind of access that should
leave a trail.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from flask import Blueprint, jsonify, request

from ..errors import AppError, MalformedRequestError
from ..extensions import db
from ..models.enums import AuditOutcome, ReportStatus
from ..schemas.responses import (
    serialize_assignment,
    serialize_case_status_entry,
    serialize_dispatch,
    serialize_incident_detail,
    serialize_incident_summary,
)
from ..security.context import authenticated, current_principal, require_principal
from ..utils.correlation import current_request_id
from .dependencies import audit_repository, case_service, incident_service

bp = Blueprint("incidents", __name__)

MAX_PAGE = 200


def _status_filter() -> tuple[ReportStatus, ...] | None:
    """`?status=triaged,under_review` — narrows the open queue, never widens it.

    Absent means "everything open," which is the existing behaviour untouched.
    """
    raw = request.args.get("status")
    if not raw:
        return None
    try:
        return tuple(ReportStatus(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError:
        raise MalformedRequestError(
            "status must be a comma-separated list of: "
            + ", ".join(status.value for status in ReportStatus)
        ) from None


def _audit(action: str, object_id: str, outcome: AuditOutcome, **detail) -> None:
    """Record a responder action.

    `detail` carries states and counts. Never a narrative, never a coordinate,
    never a storage path — an audit log holding those would be a second copy of
    the material it exists to protect, kept somewhere with different access
    rules.
    """
    principal = current_principal()
    audit_repository().record(
        action=action,
        object_type="incident",
        object_id=object_id,
        outcome=outcome,
        actor_user_id=principal.user_id if principal else None,
        actor_role=principal.role if principal else None,
        request_id=current_request_id(),
        detail=detail or None,
    )


@contextmanager
def _audited(action: str, object_id: str):
    """Write a DENIED audit row when the service refuses, then re-raise.

    A refused attempt is the one most worth recording: it is what a probe looks
    like. The evidence route has done this since 4B-1; the responder routes now
    match it rather than logging only the accesses that succeeded.

    The rollback matters — the service may have left a partial transaction, and
    the audit row must survive the failure that caused it.
    """
    try:
        yield
    except AppError as exc:
        if exc.status_code in (401, 403, 404, 409):
            db.session.rollback()
            _audit(action, object_id, AuditOutcome.DENIED, reason=exc.code)
            db.session.commit()
        raise


def _page_args() -> tuple[int, int]:
    try:
        limit = min(int(request.args.get("limit", 100)), MAX_PAGE)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        raise MalformedRequestError("limit and offset must be integers.") from None
    return max(limit, 1), offset


@bp.get("/incidents")
@authenticated
def list_incidents():
    """The open queue for the caller's role.

    Ordered by the incident — emergency, then ongoing, then most recent. Never
    by anything about the reporter.
    """
    require_principal()
    limit, offset = _page_args()
    statuses = _status_filter()

    with _audited("incident.queue_view", "queue"):
        rows, total = incident_service().queue(
            current_principal(), limit=limit, offset=offset, statuses=statuses
        )

    _audit(
        "incident.queue_view",
        "queue",
        AuditOutcome.SUCCESS,
        returned=len(rows),
        status_filter=[s.value for s in statuses] if statuses else None,
    )
    db.session.commit()

    return (
        jsonify(
            {
                "items": [serialize_incident_summary(row) for row in rows],
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "returned": len(rows),
                },
                # Said by the server, not assumed by the client: with no verified
                # coordinates there is nothing to draw, and the responder
                # interface must say so rather than render an empty campus and
                # let it read as "no incidents".
                "mapping_available": any(row.location.latitude is not None for row in rows),
            }
        ),
        200,
    )


@bp.get("/incidents/<string:public_ref>")
@authenticated
def get_incident(public_ref: str):
    """One incident, with its destination and evidence ids."""
    require_principal()

    with _audited("incident.view", public_ref):
        detail = incident_service().detail(current_principal(), public_ref)

    _audit(
        "incident.view",
        public_ref,
        AuditOutcome.SUCCESS,
        narrative_read=detail.narrative_available,
        evidence_count=len(detail.evidence_ids),
    )
    db.session.commit()

    return jsonify(serialize_incident_detail(detail)), 200


@bp.post("/incidents/<string:public_ref>/dispatch")
@authenticated
def raise_dispatch(public_ref: str):
    """Open a dispatch.

    A human action, always. Nothing in this system opens a dispatch on its own —
    not on an emergency flag, not on a category, not on any score. Someone
    decides to go.
    """
    require_principal()

    with _audited("dispatch.raised", public_ref):
        dispatch = incident_service().raise_dispatch(current_principal(), public_ref)

    _audit("dispatch.raised", public_ref, AuditOutcome.SUCCESS, state=dispatch.state.value)
    db.session.commit()

    return jsonify(serialize_dispatch(dispatch)), 201


@bp.post("/incidents/<string:public_ref>/dispatch/state")
@authenticated
def advance_dispatch(public_ref: str):
    """Acknowledge, set off, arrive, close, or stand down."""
    require_principal()

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "state" not in body:
        raise MalformedRequestError("Send a JSON body with a 'state' field.")

    service = incident_service()
    target = service.parse_dispatch_state(str(body["state"]))
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise MalformedRequestError("'note' must be a string.")

    with _audited(f"dispatch.{target.value}", public_ref):
        dispatch = service.advance_dispatch(
            current_principal(), public_ref, target, note=note or None
        )

    _audit(
        f"dispatch.{target.value}",
        public_ref,
        AuditOutcome.SUCCESS,
        state=dispatch.state.value,
    )
    db.session.commit()

    return jsonify(serialize_dispatch(dispatch)), 200


@bp.post("/incidents/<string:public_ref>/category")
@authenticated
def override_category(public_ref: str):
    """Record that a responder judged the category differently from the model.

    This does not change the report. What the student chose stays on
    `core.report`; the responder's judgement is recorded against the suggestion,
    attributed and timestamped. Changing the report's own category belongs to
    case management, with its own history trail.
    """
    require_principal()

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("category_id"), int):
        raise MalformedRequestError("Send a JSON body with an integer 'category_id'.")

    with _audited("triage.category_override", public_ref):
        incident_service().override_category(current_principal(), public_ref, body["category_id"])

    _audit(
        "triage.category_override",
        public_ref,
        AuditOutcome.SUCCESS,
        category_id=body["category_id"],
    )
    db.session.commit()
    return "", 204


@bp.post("/incidents/<string:public_ref>/links/<int:link_id>")
@authenticated
def review_link(public_ref: str, link_id: int):
    """Confirm or reject a proposed link between two reports.

    Nothing acts on an unreviewed link. A responder can only review one whose
    other side they could already open.
    """
    require_principal()

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("confirmed"), bool):
        raise MalformedRequestError("Send a JSON body with a boolean 'confirmed'.")

    action = "triage.link_confirmed" if body["confirmed"] else "triage.link_rejected"
    with _audited(action, public_ref):
        incident_service().review_link(current_principal(), public_ref, link_id, body["confirmed"])

    _audit(action, public_ref, AuditOutcome.SUCCESS, link_id=link_id)
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# Case lifecycle
#
# Distinct from dispatch above: `POST .../status` moves `core.report.
# current_status` through submitted → … → resolved (via a `core.case_status_
# history` insert; the row is never written directly — see `CaseService`).
# `POST .../assign` and `.../unassign` manage `core.case_assignment`, which
# answers "who owns this case" and is independent of both case status and
# dispatch status. None of these three touch `core.emergency_dispatch`.
# ---------------------------------------------------------------------------


@bp.post("/incidents/<string:public_ref>/status")
@authenticated
def change_case_status(public_ref: str):
    """Move a case to its next legal status.

    `target` is required. `remark` is required for every transition except the
    first acknowledgement (`submitted` → `triaged`). `resolution_reason` is
    required exactly when `target` is a terminal status and forbidden
    otherwise — the service enforces both, and the database enforces the
    resolution-reason half again regardless of what the service does.
    """
    require_principal()

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "target" not in body:
        raise MalformedRequestError("Send a JSON body with a 'target' field.")

    service = case_service()
    target = service.parse_status(str(body["target"]))

    remark = body.get("remark")
    if remark is not None and not isinstance(remark, str):
        raise MalformedRequestError("'remark' must be a string.")

    raw_reason = body.get("resolution_reason")
    if raw_reason is not None and not isinstance(raw_reason, str):
        raise MalformedRequestError("'resolution_reason' must be a string.")
    resolution_reason = service.parse_resolution_reason(raw_reason) if raw_reason else None

    visible = body.get("visible_to_reporter", True)
    if not isinstance(visible, bool):
        raise MalformedRequestError("'visible_to_reporter' must be a boolean.")

    with _audited("case.status_change", public_ref):
        entry = service.change_status(
            current_principal(),
            public_ref,
            target,
            remark=remark,
            resolution_reason=resolution_reason,
            visible_to_reporter=visible,
        )

    # The remark and any resolution detail live in the database row, not in the
    # audit log — `detail` here is metadata about the transition, not the
    # transition's content.
    _audit(
        "case.status_change",
        public_ref,
        AuditOutcome.SUCCESS,
        from_status=entry.from_status.value if entry.from_status else None,
        to_status=entry.to_status.value,
        resolution_reason=entry.resolution_reason.value if entry.resolution_reason else None,
    )
    db.session.commit()

    return jsonify(serialize_case_status_entry(entry)), 201


@bp.post("/incidents/<string:public_ref>/assign")
@authenticated
def assign_case(public_ref: str):
    """Claim a case, or hand it to a named colleague.

    An empty body assigns to the caller — "assign to me," the common case in a
    shared queue. `assignee_user_id` assigns to someone else instead; the
    service refuses a target whose role does not match how the report is
    routed, and the database refuses a student regardless of what either says.
    """
    require_principal()

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        raise MalformedRequestError("Request body must be a JSON object.")

    raw_assignee = body.get("assignee_user_id")
    if raw_assignee is not None and not isinstance(raw_assignee, str):
        raise MalformedRequestError("'assignee_user_id' must be a string.")
    assignee_user_id = None
    if raw_assignee:
        try:
            assignee_user_id = uuid.UUID(raw_assignee)
        except ValueError:
            raise MalformedRequestError("'assignee_user_id' must be a UUID.") from None

    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise MalformedRequestError("'note' must be a string.")

    with _audited("case.assign", public_ref):
        view = case_service().assign(
            current_principal(), public_ref, assignee_user_id=assignee_user_id, note=note
        )

    _audit(
        "case.assign",
        public_ref,
        AuditOutcome.SUCCESS,
        assignee_role=view.assignee.role.value,
        self_assigned=assignee_user_id is None,
    )
    db.session.commit()

    return jsonify(serialize_assignment(view)), 201


@bp.post("/incidents/<string:public_ref>/unassign")
@authenticated
def unassign_case(public_ref: str):
    """Release the active assignment, returning the case to the unowned queue."""
    require_principal()

    body = request.get_json(silent=True) or {}
    note = body.get("note") if isinstance(body, dict) else None
    if note is not None and not isinstance(note, str):
        raise MalformedRequestError("'note' must be a string.")

    with _audited("case.unassign", public_ref):
        case_service().unassign(current_principal(), public_ref, note=note)

    _audit("case.unassign", public_ref, AuditOutcome.SUCCESS)
    db.session.commit()
    return "", 204
