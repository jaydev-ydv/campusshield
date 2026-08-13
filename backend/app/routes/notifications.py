"""Notification endpoints — a student or responder's own in-app inbox.

Two endpoints, both scoped to the caller. There is no endpoint that lists
another user's notifications, and no admin view of the notification table —
nothing in this phase needs one.

Not audited to `audit.access_log`, deliberately, matching `GET /reports/mine`:
a person reading or acknowledging their own low-sensitivity notification list
is not the kind of access this codebase's audit trail exists to catch. The
sensitive event already has its own audit row, written where the notification
was created — `case.status_change` or `case.assign` — via `routes/incidents.py`.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request

from ..errors import MalformedRequestError, NotFoundError
from ..extensions import db
from ..schemas.requests import PaginationQuerySchema
from ..schemas.responses import paginated, serialize_notification
from ..security.context import authenticated, require_principal
from .dependencies import notification_repository

bp = Blueprint("notifications", __name__)


@bp.get("/notifications")
@authenticated
def list_notifications():
    """The caller's own notifications, most recent first."""
    principal = require_principal()
    query = PaginationQuerySchema().load(request.args.to_dict())

    raw_unread = request.args.get("unread_only", "false")
    unread_only = raw_unread.lower() in ("1", "true", "yes")

    repo = notification_repository()
    rows = repo.list_for_user(
        principal.user_id,
        unread_only=unread_only,
        limit=query["limit"],
        offset=query["offset"],
    )
    total = repo.count_for_user(principal.user_id, unread_only=unread_only)
    db.session.commit()

    body = paginated(
        [serialize_notification(row) for row in rows],
        total=total,
        limit=query["limit"],
        offset=query["offset"],
    )
    # Always the true unread count, independent of `unread_only` — the bell
    # badge needs this number regardless of which list view is open.
    body["unread_count"] = repo.count_unread(principal.user_id)
    return jsonify(body), 200


@bp.post("/notifications/<string:notification_id>/read")
@authenticated
def mark_notification_read(notification_id: str):
    """Mark one of the caller's own notifications as read.

    404 for someone else's notification, identically to a nonexistent one —
    the same oracle-avoidance pattern used for reports and evidence.
    """
    principal = require_principal()

    try:
        parsed_id = uuid.UUID(notification_id)
    except ValueError:
        raise MalformedRequestError("notification_id must be a UUID.") from None

    repo = notification_repository()
    notification = repo.get_own(parsed_id, principal.user_id)
    if notification is None:
        raise NotFoundError("No such notification.")

    repo.mark_read(notification)
    db.session.commit()
    return "", 204
