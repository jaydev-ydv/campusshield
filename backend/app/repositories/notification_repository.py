"""Data access for `notify.notification`.

Every read here is scoped to a single `recipient_user_id` supplied by the
caller. There is no method that lists notifications across users — the
equivalent of `ReportRepository.list_for_reporter`, not of an admin inbox —
because nothing in this phase needs one and building it would be surface
area with no user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ..models import Notification, Report
from ..models.enums import DeliveryState, NotificationAudience, NotificationCategory


@dataclass(frozen=True, slots=True)
class NotificationRow:
    """A notification plus the public reference it points at.

    `related_report_id` is never returned to a client — it is an internal
    UUID, and this codebase's rule is that no internal id crosses the
    response boundary. The repository resolves it to `public_ref` here, once,
    rather than leaving every caller to remember not to leak the raw column.
    """

    notification: Notification
    related_public_ref: str | None


class NotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- writes --------------------------------------------------------

    def create_for_user(
        self,
        user_id: uuid.UUID,
        *,
        category: NotificationCategory,
        title: str,
        body: str,
        related_report_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> Notification:
        """Write one notification, immediately marked delivered to the API.

        `delivery_state=SENT` here means "available through `GET
        /notifications`," not "pushed to a device" — see the module docstring
        on `Notification`. `fcm_message_id` is never set by this method.
        """
        now = now or datetime.now(timezone.utc)
        row = Notification(
            audience=NotificationAudience.USER,
            recipient_user_id=user_id,
            category=category,
            title=title,
            body=body,
            related_report_id=related_report_id,
            created_at=now,
            delivery_state=DeliveryState.SENT,
            sent_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def mark_read(self, notification: Notification, *, now: datetime | None = None) -> None:
        if notification.read_at is not None:
            return
        notification.read_at = now or datetime.now(timezone.utc)
        self._session.flush()

    # -- reads -----------------------------------------------------------

    def _own(self, user_id: uuid.UUID) -> Select[tuple[Notification]]:
        return select(Notification).where(
            Notification.audience == NotificationAudience.USER,
            Notification.recipient_user_id == user_id,
        )

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationRow]:
        # `related_public_ref` is resolved with a single outer join rather
        # than one query per row — an inner join would be wrong, since a
        # notification's `related_report_id` is nullable (a `system`-category
        # notification, say, has nothing to link to).
        stmt = (
            select(Notification, Report.public_ref)
            .outerjoin(Report, Report.report_id == Notification.related_report_id)
            .where(
                Notification.audience == NotificationAudience.USER,
                Notification.recipient_user_id == user_id,
            )
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)

        return [
            NotificationRow(notification=notification, related_public_ref=public_ref)
            for notification, public_ref in self._session.execute(stmt).all()
        ]

    def count_unread(self, user_id: uuid.UUID) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.audience == NotificationAudience.USER,
                    Notification.recipient_user_id == user_id,
                    Notification.read_at.is_(None),
                )
            )
            or 0
        )

    def count_for_user(self, user_id: uuid.UUID, *, unread_only: bool = False) -> int:
        stmt = self._own(user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        inner = stmt.subquery()
        return self._session.scalar(select(func.count()).select_from(inner)) or 0

    def get_own(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> Notification | None:
        """Fetch a notification, only if it belongs to the caller.

        Returns `None` for someone else's notification exactly as it would for
        a nonexistent one — the route turns both into the same 404, so this
        cannot be used to probe whether an id exists.
        """
        return self._session.scalar(
            select(Notification).where(
                Notification.notification_id == notification_id,
                Notification.recipient_user_id == user_id,
            )
        )
