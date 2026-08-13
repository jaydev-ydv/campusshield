"""Writes to ``audit.access_log``.

The table is append-only — triggers block UPDATE and DELETE, and the application
role holds INSERT only — so this repository has exactly one write method and no
update path.

``audit.access_log`` carries no foreign keys, deliberately: every ``ON DELETE``
action conflicts with append-only enforcement, and audit records must outlive the
rows they describe.  Identifiers are stored as bare values.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models.enums import AuditOutcome, UserRole


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        action: str,
        object_type: str,
        object_id: str,
        outcome: AuditOutcome,
        actor_user_id: uuid.UUID | None = None,
        actor_role: UserRole | None = None,
        ip_hash: str | None = None,
        user_agent_hash: str | None = None,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append one audit row.

        ``detail`` must never carry narrative text.  It exists for metadata —
        a justification, a constraint name, a count — and anything else turns the
        audit log into a second, unguarded copy of the reports.
        """
        import json

        self._session.execute(
            text(
                """
                INSERT INTO audit.access_log
                    (actor_user_id, actor_role, action, object_type, object_id,
                     outcome, ip_hash, user_agent_hash, request_id, detail)
                VALUES
                    (:actor_user_id, :actor_role, :action, :object_type, :object_id,
                     :outcome, :ip_hash, :user_agent_hash, :request_id, CAST(:detail AS jsonb))
                """
            ),
            {
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "actor_role": actor_role.value if actor_role else None,
                "action": action,
                "object_type": object_type,
                "object_id": object_id,
                "outcome": outcome.value,
                "ip_hash": ip_hash,
                "user_agent_hash": user_agent_hash,
                "request_id": request_id if _is_uuid(request_id) else None,
                "detail": json.dumps(detail) if detail else None,
            },
        )


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
