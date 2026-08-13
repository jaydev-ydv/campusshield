"""Account provisioning and session identity.

Two endpoints, both thin:

``POST /auth/register`` — the only endpoint that accepts a verified credential
without requiring an existing account, because that is the state a newly signed
up Firebase user is in. The role it assigns is a server-side constant.

``GET /auth/me`` — what the client is allowed to believe about itself. The
frontend must never infer its own role: it asks, and the answer comes from
``identity.app_user``.

``PATCH /auth/me`` — the one editable field on an account: `display_name`,
and only for authority roles. Students cannot reach this successfully —
`AccountService` refuses before any write, on `role`, the same signal the
database's own CHECK constraint enforces underneath it.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..errors import AuthenticationError, MalformedRequestError
from ..extensions import db, limiter
from ..models.enums import AuditOutcome
from ..schemas.requests import RegisterAccountSchema, UpdateProfileSchema
from ..schemas.responses import serialize_principal
from ..security.context import authenticated, require_principal
from ..utils.correlation import current_request_id
from .dependencies import account_service, audit_repository

bp = Blueprint("auth", __name__)


@bp.post("/auth/register")
# Stricter than the global default: account provisioning is a one-time act
# per real person, so sustained volume from one address is the abuse case,
# not a legitimate burst.
@limiter.limit("10 per hour")
def register():
    """Create the application account behind an already-verified credential.

    Registration is a two-step act by design: the client creates the Firebase
    credential, then presents its ID token here to be given an application
    account. Splitting it that way means this service never sees, handles, or
    stores a password.

    The request body cannot set a role. It cannot set a role by omission, by a
    different spelling, or by any other field — the schema rejects unknown keys
    and `AccountService` assigns `student` as a constant.
    """
    provider = current_app.extensions["auth_provider"]
    credential = provider.extract_credential(request.headers)
    if not credential:
        raise AuthenticationError(
            "Sign in with Firebase first, then present the ID token here.",
            details={"expected": "Authorization: Bearer <Firebase ID token>"},
        )

    body: dict = {}
    if request.data:
        if not request.is_json:
            raise MalformedRequestError("Content-Type must be application/json.")
        parsed = request.get_json(silent=True)
        if parsed is None or not isinstance(parsed, dict):
            raise MalformedRequestError("Request body must be a JSON object.")
        body = parsed
    payload = RegisterAccountSchema().load(body)

    subject = provider.identify(credential)
    if subject is None:
        raise AuthenticationError("No credential was presented.")

    principal, created = account_service().provision(subject, fallback_email=payload.get("email"))

    audit_repository().record(
        action="account.provision" if created else "account.provision_noop",
        object_type="user",
        object_id=str(principal.user_id),
        outcome=AuditOutcome.SUCCESS,
        actor_user_id=principal.user_id,
        actor_role=principal.role,
        request_id=current_request_id(),
    )
    db.session.commit()

    return jsonify(serialize_principal(principal, created=created)), 201 if created else 200


@bp.get("/auth/me")
@authenticated
def me():
    """The caller's own identity and role, from the database.

    The client asks rather than infers. A role held in browser state is a claim
    the browser is making about itself, and the only place that answer is
    trustworthy is `identity.app_user`.
    """
    return jsonify(serialize_principal(require_principal())), 200


@bp.patch("/auth/me")
@authenticated
def update_me():
    """Set the caller's own display name.

    Refused for students with a 403, not a 400: the field is well-formed, the
    role simply does not carry one. See `AccountService.update_display_name`.
    """
    principal = require_principal()

    if not request.is_json:
        raise MalformedRequestError("Content-Type must be application/json.")
    parsed = request.get_json(silent=True)
    if parsed is None or not isinstance(parsed, dict):
        raise MalformedRequestError("Request body must be a JSON object.")
    payload = UpdateProfileSchema().load(parsed)

    updated = account_service().update_display_name(principal, payload["display_name"].strip())

    audit_repository().record(
        action="account.update_profile",
        object_type="user",
        object_id=str(updated.user_id),
        outcome=AuditOutcome.SUCCESS,
        actor_user_id=updated.user_id,
        actor_role=updated.role,
        request_id=current_request_id(),
    )
    db.session.commit()

    return jsonify(serialize_principal(updated)), 200
