from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base
from .enums import PolicyOrigin, pg_enum


class SystemPolicy(Base):
    """``core.system_policy`` — runtime-configurable thresholds and retention.

    ``origin`` distinguishes a value the project chose from one an institution
    mandated.  Every row seeded by the migration is ``prototype_default``, and
    nothing in this prototype may claim otherwise: the project specification
    defines no retention period, so the code does not assert one.
    """

    __tablename__ = "system_policy"
    __table_args__ = {"schema": "core"}

    policy_key: Mapped[str] = mapped_column(String, primary_key=True)
    policy_value: Mapped[str] = mapped_column(String, nullable=False)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[PolicyOrigin] = mapped_column(
        pg_enum(PolicyOrigin, "policy_origin"), nullable=False
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    min_value: Mapped[str | None] = mapped_column(String)
    max_value: Mapped[str | None] = mapped_column(String)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def as_int(self) -> int:
        return int(self.policy_value)
