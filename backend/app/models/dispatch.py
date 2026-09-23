"""Responder-side tables: dispatch and location detail.

Both existed in the schema before this phase — `core.emergency_dispatch` since
`0001`, `core.report_location_detail` since `0003`. Neither was mapped, because
nothing wrote to them yet. They are mapped here, not created here.

Note what is absent from both: any reference to the reporter. A dispatch records
who *responded*, never who reported. For an anonymous report there is nothing to
record, and for an identified one recording it here would put the reporter's
identity in a table responders read routinely.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base
from .enums import DispatchState, LocationResolution, LocationSignalSource, pg_enum


class EmergencyDispatch(Base):
    """``core.emergency_dispatch`` — one responder engagement with one report.

    The state machine and every timestamp were already modelled in `0001`. This
    phase supplies the writer the design always assumed and changes nothing about
    the shape.

    `contact_attempted` is a guard rather than a workflow field: it records that a
    responder tried to reach the reporter, which is only ever legitimate when
    `core.report.reporter_contactable` is true — and a CHECK constraint on
    `core.report` makes that false for every anonymous report.
    """

    __tablename__ = "emergency_dispatch"
    __table_args__ = {"schema": "core"}

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), nullable=False
    )
    state: Mapped[DispatchState] = mapped_column(
        pg_enum(DispatchState, "dispatch_state"), nullable=False, server_default=text("'pending'")
    )
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity.app_user.user_id")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    on_scene_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responder_note: Mapped[str | None] = mapped_column(String)
    public_note: Mapped[str | None] = mapped_column(String)
    contact_attempted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    outcome_summary: Mapped[str | None] = mapped_column(String)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EmergencyDispatch {self.dispatch_id} {self.state.value}>"


class ReportLocationDetail(Base):
    """``core.report_location_detail`` — what corroborating signals said.

    A separate table from `core.report` for the same reason `core.report_narrative`
    is separate: so `SELECT` can be revoked on it independently. Analytics can
    compute every hotspot from `location_id` without ever reading a precise
    coordinate.

    **This table never decides where an incident is.** `core.report.location_id`
    does. `signal_latitude`/`signal_longitude` are the coordinate the *signal*
    carried — a photograph's EXIF, say — kept so a responder can see why a
    conflict was raised, not so anything can navigate to it.
    """

    __tablename__ = "report_location_detail"
    __table_args__ = {"schema": "core"}

    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), primary_key=True
    )
    resolution: Mapped[LocationResolution] = mapped_column(
        pg_enum(LocationResolution, "location_resolution"), nullable=False
    )
    source: Mapped[LocationSignalSource] = mapped_column(
        pg_enum(LocationSignalSource, "location_signal_source"), nullable=False
    )
    signal_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    signal_longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    distance_m: Mapped[int | None] = mapped_column(Integer)
    signal_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict_note: Mapped[str | None] = mapped_column(String)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ReportLocationDetail {self.report_id} {self.resolution.value}>"
