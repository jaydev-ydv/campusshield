"""Error types and the single JSON error shape the API returns.

Every failure — validation, authorisation, a database constraint, or an
unhandled exception — leaves through here and looks the same to a client:

    {
      "error": {
        "code": "VALIDATION_ERROR",
        "message": "human readable",
        "details": {...},          # optional
        "request_id": "uuid"
      }
    }

The request_id is also on the ``X-Request-ID`` response header, so a student can
quote it from a screenshot and it can be found in the logs.

One rule governs the messages: a client is told what it did wrong, never what
exists.  "report not found" is returned both when a report does not exist and
when it exists but belongs to somebody else, because distinguishing the two
turns the endpoint into an oracle for whether a given reference is real.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, g, jsonify
from marshmallow import ValidationError as MarshmallowValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors the API reports deliberately."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code
        self.details = details or {}


class ValidationError(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"
    message = "The request body failed validation."


class MalformedRequestError(AppError):
    status_code = 400
    code = "MALFORMED_REQUEST"
    message = "The request could not be parsed."


class AuthenticationError(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    message = "Authentication is required."


class AuthorizationError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have access to this resource."


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "The requested resource does not exist."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "The request conflicts with the current state."


class ImmutableStateError(AppError):
    """Attempt to change something the data model declares immutable.

    Distinct from ConflictError because the cases matter: changing a report's
    submission_mode, or attaching an identity to an anonymous report, are not
    ordinary conflicts — they are attempts to undo an anonymity guarantee.
    """

    status_code = 409
    code = "IMMUTABLE_STATE"
    message = "This value cannot be changed after the record was created."


class QuotaExceededError(AppError):
    status_code = 429
    code = "QUOTA_EXCEEDED"
    message = "You have reached today's submission limit."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "A dependency is unavailable."


# Database constraint names mapped to the client-facing error they represent.
# The schema enforces these regardless of what the application does; when one
# fires it means a request slipped past a service-layer check, so it is logged at
# WARNING as well as returned.
_CONSTRAINT_ERRORS: dict[str, tuple[type[AppError], str]] = {
    "ck_report_anonymous_is_not_contactable": (
        ImmutableStateError,
        "An anonymous report cannot be marked contactable.",
    ),
    "ck_report_ongoing_requires_emergency": (
        ValidationError,
        "is_ongoing may only be set on an emergency report.",
    ),
    "ck_report_occurred_not_future": (
        ValidationError,
        "occurred_at cannot be in the future.",
    ),
    "ck_report_category_emergency_is_incident": (
        ValidationError,
        "Only incident categories may be raised as emergencies.",
    ),
    "ck_narrative_length": (
        ValidationError,
        "The narrative must be between 10 and 8000 characters.",
    ),
    "uq_report_public_ref": (ConflictError, "Reference collision; please retry."),
}


def _constraint_name(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name:
        return str(name)
    text = str(orig or exc)
    for known in _CONSTRAINT_ERRORS:
        if known in text:
            return known
    return None


def _payload(err: AppError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": err.code,
        "message": err.message,
        "request_id": g.get("request_id", "unknown"),
    }
    if err.details:
        body["details"] = err.details
    return {"error": body}


def _respond(err: AppError):
    response = jsonify(_payload(err))
    response.status_code = err.status_code
    response.headers["X-Request-ID"] = g.get("request_id", "unknown")
    return response


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _handle_app_error(exc: AppError):
        if exc.status_code >= 500:
            logger.exception("application error: %s", exc.message)
        return _respond(exc)

    @app.errorhandler(MarshmallowValidationError)
    def _handle_marshmallow(exc: MarshmallowValidationError):
        return _respond(
            ValidationError(
                "The request body failed validation.",
                details={"fields": exc.messages},
            )
        )

    @app.errorhandler(IntegrityError)
    def _handle_integrity(exc: IntegrityError):
        db_session_rollback(app)
        name = _constraint_name(exc)
        if name and name in _CONSTRAINT_ERRORS:
            cls, message = _CONSTRAINT_ERRORS[name]
            logger.warning(
                "database constraint %s rejected a request that passed service "
                "validation; the schema caught it, but the service layer should have",
                name,
            )
            return _respond(cls(message, details={"constraint": name}))
        logger.exception("unmapped integrity error")
        return _respond(ConflictError("The request conflicts with existing data."))

    @app.errorhandler(DBAPIError)
    def _handle_dbapi(exc: DBAPIError):
        db_session_rollback(app)
        logger.exception("database error")
        return _respond(ServiceUnavailableError("The database is unavailable."))

    @app.errorhandler(HTTPException)
    def _handle_http(exc: HTTPException):
        mapped = {
            400: MalformedRequestError,
            401: AuthenticationError,
            403: AuthorizationError,
            404: NotFoundError,
            405: AppError,
            409: ConflictError,
            413: ValidationError,
            415: MalformedRequestError,
        }.get(exc.code or 500, AppError)
        err = mapped(exc.description or mapped.message)
        err.status_code = exc.code or 500
        if exc.code == 405:
            err.code = "METHOD_NOT_ALLOWED"
        return _respond(err)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        db_session_rollback(app)
        logger.exception("unhandled exception")
        # Deliberately opaque: an internal error message can disclose table
        # names, query fragments, or file paths.  The request_id is the bridge
        # between what the user sees and what the logs hold.
        return _respond(AppError())


def db_session_rollback(app: Flask) -> None:
    """Roll back the request's session so the connection is reusable."""
    try:
        from .extensions import db

        db.session.rollback()
    except Exception:  # pragma: no cover - best effort during error handling
        app.logger.warning("failed to roll back session during error handling")
