"""``notify.notification`` — existed since `0001`, mapped here because Phase 4E
is the first thing that writes to it.

Two rules this table's own design already enforced before this phase touched
it, both worth restating because they shaped everything built on top:

**The body must never carry narrative text.** A notification is read wherever
the recipient happens to be, possibly in front of someone else — a lock
screen, a shared laptop, a glance from across a room. `NotificationService`
builds `title`/`body` from a fixed template (a status label and a reference),
never from a report's account of what happened.

**Anonymous reporters cannot be notified, by construction.** `recipient_user_id`
is a foreign key to `identity.app_user`. An anonymous report has no row in
`identity.report_attribution` — there is no user to address, and nothing here
tries to invent one. Building a workaround (e.g. binding a device token to a
report instead of a user) was explicitly considered and rejected when this
table was designed, because it would quietly rebuild the very link anonymity
exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base
from .enums import DeliveryState, NotificationAudience, NotificationCategory, UserRole, pg_enum


class Notification(Base):
    """One deliverable message. This project writes ``audience='user'`` only."""

    __tablename__ = "notification"
    __table_args__ = {"schema": "notify"}

    notification_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    audience: Mapped[NotificationAudience] = mapped_column(
        pg_enum(NotificationAudience, "notification_audience"), nullable=False
    )
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.app_user.user_id", ondelete="CASCADE")
    )
    recipient_role: Mapped[UserRole | None] = mapped_column(pg_enum(UserRole, "user_role"))
    recipient_zone_id: Mapped[int | None] = mapped_column(ForeignKey("core.campus_zone.zone_id"))
    category: Mapped[NotificationCategory] = mapped_column(
        pg_enum(NotificationCategory, "notification_category"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    related_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE")
    )
    related_hotspot_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    related_alert_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    delivery_state: Mapped[DeliveryState] = mapped_column(
        pg_enum(DeliveryState, "delivery_state"), nullable=False, server_default=text("'pending'")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fcm_message_id: Mapped[str | None] = mapped_column(String)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Notification {self.notification_id} {self.category.value}>"
