"""Evidence endpoints.

Three, all thin. Parse, authorise, delegate, audit, commit.

The client never sees or supplies a storage path. Uploads return an opaque
capability token; retrieval takes an evidence id and the server resolves the path
itself, only after `can_view_report` has admitted the caller.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, Response, current_app, jsonify, request

from ..errors import MalformedRequestError, NotFoundError, ValidationError
from ..extensions import db, limiter
from ..models.enums import AuditOutcome
from ..security.authorization import can_view_report
from ..security.context import authenticated, current_principal, report_token, require_principal
from ..utils.correlation import current_request_id
from ..utils.references import hash_access_token
from .dependencies import (
    audit_repository,
    evidence_service,
    report_repository,
    report_token_repository,
)

bp = Blueprint("evidence", __name__)

UPLOAD_FIELD = "file"


def _audit(action: str, object_id: str, outcome: AuditOutcome, **detail) -> None:
    """Record an evidence action.

    `detail` carries counts and content types only. Never bytes, never a storage
    path, never a narrative, never an EXIF value — an audit log that holds those
    is a second unguarded copy of the thing it exists to protect.
    """
    principal = current_principal()
    audit_repository().record(
        action=action,
        object_type="evidence",
        object_id=object_id,
        outcome=outcome,
        actor_user_id=principal.user_id if principal else None,
        actor_role=principal.role if principal else None,
        request_id=current_request_id(),
        detail=detail or None,
    )


@bp.post("/evidence")
@authenticated
# Stricter than the global default: each call costs storage and sanitisation
# work regardless of outcome, so it is the highest-cost-per-request route in
# the API.
@limiter.limit("30 per hour")
def upload_evidence():
    """Upload one image.

    Returns an opaque token. The image is stored but attached to nothing until a
    report is submitted carrying that token — an upload that is never submitted
    is not evidence, and is reaped.
    """
    require_principal()

    if UPLOAD_FIELD not in request.files:
        raise MalformedRequestError(
            f"Send the image as multipart form data in a '{UPLOAD_FIELD}' field."
        )

    uploaded = request.files[UPLOAD_FIELD]
    data = uploaded.read()
    if not data:
        raise ValidationError(
            "The file is empty.", details={"fields": {"file": ["No data received."]}}
        )

    # `uploaded.filename` and `uploaded.content_type` are client claims. The
    # filename is read for nothing at all — it is never persisted, never used to
    # build a storage path, and never logged. The content type is passed only so
    # the sanitiser can note a mismatch; the bytes decide.
    result = evidence_service().upload(data, declared_content_type=uploaded.content_type)

    _audit(
        "evidence.upload",
        "pending",
        AuditOutcome.SUCCESS,
        content_type=result.content_type,
        byte_size=result.byte_size,
    )
    db.session.commit()

    return (
        jsonify(
            {
                "upload_token": result.token,
                "content_type": result.content_type,
                "byte_size": result.byte_size,
                "width": result.width,
                "height": result.height,
                "notice": (
                    "Hidden location and device data have been removed from this image. "
                    "Anything visible in the picture itself is unchanged."
                ),
            }
        ),
        201,
    )


@bp.delete("/evidence/<string:upload_token>")
@authenticated
def discard_evidence(upload_token: str):
    """Remove a staged upload before submission — the wizard's "Remove"."""
    require_principal()

    removed = evidence_service().discard(upload_token)
    if not removed:
        # Not distinguished from "someone else's token": telling a caller that a
        # token they do not hold is real would be an oracle.
        raise NotFoundError("No such pending upload.")

    _audit("evidence.discard", "pending", AuditOutcome.SUCCESS)
    db.session.commit()
    return "", 204


@bp.get("/evidence/<uuid:evidence_id>")
def get_evidence(evidence_id: uuid.UUID):
    """Stream an attached image to an authorised caller.

    **Streamed rather than signed.** A signed URL outlives its authorisation
    check for the length of its TTL and can be forwarded to anyone; a stream
    cannot. Every byte served passes an authorisation check that has just been
    made, and every access is one audit row. The cost is bandwidth through Flask,
    which at campus scale is a good trade — and it also means no signing key is
    required, so the deployment needs one fewer secret.
    """
    service = evidence_service()
    evidence = service.get_for_report(evidence_id)

    reports = report_repository()
    report = reports.get_by_id(evidence.report_id)
    if report is None:  # pragma: no cover - FK makes this unreachable
        raise NotFoundError("No such evidence.")

    # The same policy that governs the report governs its evidence. An anonymous
    # reporter presenting their access token is admitted; nobody else is.
    raw_token = report_token()
    via_token = False
    if raw_token:
        token = report_token_repository().resolve(hash_access_token(raw_token))
        via_token = token is not None and token.report_id == report.report_id

    principal = current_principal()
    context = reports.access_context(report, via_token=via_token)

    if not can_view_report(principal, context):
        _audit("evidence.view", str(evidence_id), AuditOutcome.DENIED)
        db.session.commit()
        # 404, matching the report endpoint. A 403 would confirm the evidence id
        # is real.
        raise NotFoundError("No such evidence.")

    payload = service.read_bytes(evidence)

    _audit(
        "evidence.view",
        str(evidence_id),
        AuditOutcome.SUCCESS,
        via_token=via_token,
        content_type=evidence.content_type,
    )
    db.session.commit()

    response = Response(payload, mimetype=evidence.content_type)
    response.headers["Content-Length"] = str(len(payload))
    # Never cached by a shared proxy, and not written to disk by the browser.
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Content-Disposition"] = "inline"
    # Belt and braces: an image served with a wrong sniffed type is an XSS vector.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@bp.get("/evidence/config")
@authenticated
def evidence_config():
    """Limits the client needs to validate before uploading.

    Served rather than hard-coded in the frontend so the two cannot disagree
    about the size cap.
    """
    return (
        jsonify(
            {
                "max_bytes": current_app.config["MAX_IMAGE_UPLOAD_BYTES"],
                "accepted_types": ["image/jpeg", "image/png", "image/webp"],
                "max_per_report": current_app.config["MAX_EVIDENCE_PER_REPORT"],
            }
        ),
        200,
    )
