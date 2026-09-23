"""Report endpoints.

Thin by design: parse, delegate, serialise, commit. Every rule about who may see
what lives in :mod:`app.security.authorization`, and every rule about how a
report is created lives in :mod:`app.services.report_service`. A route that
starts making decisions is a route that has to be audited separately from the
policy it duplicates.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..errors import MalformedRequestError
from ..extensions import db, limiter
from ..models.enums import AuditOutcome, ReporterRelationship
from ..schemas.requests import (
    AttachEvidenceSchema,
    CreateReportSchema,
    PaginationQuerySchema,
    TriggerEmergencySchema,
)
from ..schemas.responses import (
    paginated,
    serialize_report_detail,
    serialize_report_summary,
    serialize_submission,
)
from ..security.context import (
    authenticated,
    current_principal,
    optional_authentication,
    report_token,
    require_principal,
)
from ..services.report_service import ReportSubmission
from ..utils.correlation import current_request_id
from ..utils.references import hash_client_ip, hash_user_agent
from .dependencies import audit_repository, evidence_service, report_service

bp = Blueprint("reports", __name__)


def _json_body() -> dict:
    if not request.is_json:
        raise MalformedRequestError("Content-Type must be application/json.")
    try:
        body = request.get_json(silent=False)
    except Exception as exc:
        raise MalformedRequestError("Request body is not valid JSON.") from exc
    if not isinstance(body, dict):
        raise MalformedRequestError("Request body must be a JSON object.")
    return body


def _audit(action: str, object_id: str, outcome: AuditOutcome, **detail) -> None:
    from flask import current_app

    principal = current_principal()
    audit_repository().record(
        action=action,
        object_type="report",
        object_id=object_id,
        outcome=outcome,
        actor_user_id=principal.user_id if principal else None,
        actor_role=principal.role if principal else None,
        ip_hash=hash_client_ip(request.remote_addr, current_app.config.get("AUDIT_IP_PEPPER")),
        user_agent_hash=hash_user_agent(
            request.headers.get("User-Agent"), current_app.config.get("AUDIT_IP_PEPPER")
        ),
        request_id=current_request_id(),
        detail=detail or None,
    )


@bp.post("/reports")
@authenticated
def create_report():
    """Submit an incident report or a safety concern.

    Handles all four shapes in one endpoint: identified, anonymous, emergency,
    and anonymous emergency. The distinction is `anonymous` and `is_emergency` in
    the body — there is no separate anonymous endpoint, because a separate URL is
    itself a signal, and a proxy log showing which endpoint a student called
    would undo the anonymity the endpoint provides.
    """
    principal = require_principal()
    payload = CreateReportSchema().load(_json_body())

    submission = ReportSubmission(
        category_id=payload["category_id"],
        location_id=payload["location_id"],
        occurred_at=payload["occurred_at"],
        narrative=payload["narrative"],
        anonymous=payload["anonymous"],
        reporter_relationship=ReporterRelationship(payload["reporter_relationship"]),
        location_hint=payload["location_hint"],
        is_emergency=payload["is_emergency"],
        is_ongoing=payload["is_ongoing"],
        contact_consent=payload["contact_consent"],
        evidence_tokens=payload["evidence_tokens"],
    )

    result = report_service().submit(principal, submission)

    # The audit row records the public reference and the mode, never the
    # narrative and never a link from an anonymous report to this actor.
    _audit(
        "report.create",
        result.report.public_ref,
        AuditOutcome.SUCCESS,
        submission_mode=result.report.submission_mode.value,
        is_emergency=result.report.is_emergency,
    )
    db.session.commit()

    return jsonify(serialize_submission(result)), 201


@bp.get("/reports/mine")
@authenticated
def list_my_reports():
    """Reports the caller filed under their own name.

    Anonymous submissions are absent by construction — they are linked to nobody,
    so no query starting from a user id can reach them. That is the guarantee
    working, not a gap. Anonymous reporters use the token issued at submission
    against `GET /reports/<ref>`.
    """
    principal = require_principal()
    query = PaginationQuerySchema().load(request.args.to_dict())
    reports, total = report_service().list_own_reports(
        principal, limit=query["limit"], offset=query["offset"]
    )
    db.session.commit()
    return (
        jsonify(
            paginated(
                [serialize_report_summary(r) for r in reports],
                total=total,
                limit=query["limit"],
                offset=query["offset"],
            )
        ),
        200,
    )


@bp.get("/reports/<string:public_ref>")
@optional_authentication
def get_report(public_ref: str):
    """One report, if the caller is entitled to it.

    Two ways in: an authenticated caller the authorisation policy admits, or an
    anonymous reporter presenting `X-Report-Token`.

    Returns **404 for both "no such report" and "not yours"**. A 403 would
    confirm that a reference is real, and references are printed on
    acknowledgements and appear in screenshots — the endpoint would become an
    oracle for whether a given code exists.
    """
    principal = current_principal()
    token = report_token()

    detail = report_service().get_report_detail(principal, public_ref=public_ref, raw_token=token)
    _audit(
        "report.view",
        detail.report.public_ref,
        AuditOutcome.SUCCESS,
        via_token=bool(token),
        narrative_disclosed=detail.narrative_available,
    )
    db.session.commit()
    return jsonify(serialize_report_detail(detail)), 200


@bp.post("/reports/emergency")
@authenticated
@limiter.limit("10 per hour")
def trigger_emergency():
    """Raise an emergency ("SOS") report.

    The minimal-interaction counterpart to `POST /reports`: no category, no
    narrative, no location choice. `ReportService.submit_sos` fills those in —
    a system narrative, a location resolved from an optional device position
    or the "unspecified" sentinel, and `is_emergency=True` — and reuses the
    same creation path everything else in `submit()` already provides:
    quota (bypassed here, deliberately, for emergencies), the public
    reference, the append-only status trail, and the responder queue, which
    already sorts emergencies first with no changes needed here.

    A second call from the same reporter within the dedup window returns the
    same report rather than creating another — see the duplicate-press
    handling in `submit_sos`.
    """
    principal = require_principal()
    payload = TriggerEmergencySchema().load(_json_body())

    result = report_service().submit_sos(
        principal,
        latitude=payload["latitude"],
        longitude=payload["longitude"],
        reporter_relationship=ReporterRelationship(payload["reporter_relationship"]),
    )

    _audit(
        "report.emergency",
        result.report.public_ref,
        AuditOutcome.SUCCESS,
        has_location=payload["latitude"] is not None,
    )
    db.session.commit()

    return jsonify(serialize_submission(result)), 201


@bp.post("/reports/<string:public_ref>/evidence")
@authenticated
@limiter.limit("30 per hour")
def attach_evidence(public_ref: str):
    """Attach evidence to a report that already exists.

    Built for the emergency path, where evidence is explicitly optional at
    the moment of the alert and may be added once the immediate danger has
    passed — but it works for any report the caller filed under their own
    name. Reporter-only: see `can_attach_evidence` for why this is narrower
    than who may view the report.
    """
    principal = require_principal()
    payload = AttachEvidenceSchema().load(_json_body())

    report = report_service().get_report_for_reporter(principal, public_ref=public_ref)
    attached = evidence_service().attach_all(
        payload["evidence_tokens"],
        report.report_id,
        limit=current_app.config["MAX_EVIDENCE_PER_REPORT"],
    )

    _audit(
        "report.evidence_attached",
        report.public_ref,
        AuditOutcome.SUCCESS,
        count=len(attached),
    )
    db.session.commit()

    return jsonify({"evidence_ids": [str(e.evidence_id) for e in attached]}), 201
