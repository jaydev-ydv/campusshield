from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base
from .enums import UserRole, pg_enum


class AppUser(Base):
    """``identity.app_user`` — who exists and what role they hold.

    Deliberately minimal: no course, year, hostel, phone, or photo.  Nothing in
    the project requires them, and a field not collected cannot be breached.
    Students have no ``display_name`` at all, enforced by a CHECK constraint.
    """

    __tablename__ = "app_user"
    __table_args__ = {"schema": "identity"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    firebase_uid: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), nullable=False)
    institutional_email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AppUser {self.user_id} role={self.role.value}>"
