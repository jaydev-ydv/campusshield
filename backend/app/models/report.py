from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import Base
from .campus import CampusLocation
from .enums import (
    ReporterRelationship,
    ReportKind,
    ReportStatus,
    ResolutionReason,
    RiskBand,
    SubmissionMode,
    UserRole,
    pg_enum,
)


class ReportCategory(Base):
    """``core.report_category`` — taxonomy and routing.

    ``routes_to_role`` is where role-based case management actually lives: the
    category a student picks decides which authority sees the report.
    """

    __tablename__ = "report_category"
    __table_args__ = {"schema": "core"}

    category_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[ReportKind] = mapped_column(pg_enum(ReportKind, "report_kind"), nullable=False)
    routes_to_role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), nullable=False)
    base_severity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    requires_confidentiality: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    emergency_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class Report(Base):
    """``core.report`` — the central table.

    **There is no user column here, and there must never be one.**  A report is
    anonymous because no row exists in ``identity.report_attribution``; there is
    nothing on this model to leak, and ``SELECT *`` cannot expose an identity
    that is not present.

    ``reporter_contactable`` answers the one operational question a responder has
    about an emergency — can I reach this person? — without the identity schema
    entering the emergency path at all.  A database CHECK makes ``False`` the
    only legal value for an anonymous report.
    """

    __tablename__ = "report"
    __table_args__ = {"schema": "core"}

    report_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    public_ref: Mapped[str] = mapped_column(String, nullable=False)
    report_kind: Mapped[ReportKind] = mapped_column(
        pg_enum(ReportKind, "report_kind"), nullable=False
    )
    submission_mode: Mapped[SubmissionMode] = mapped_column(
        pg_enum(SubmissionMode, "submission_mode"), nullable=False
    )
    reporter_relationship: Mapped[ReporterRelationship] = mapped_column(
        pg_enum(ReporterRelationship, "reporter_relationship"),
        nullable=False,
        server_default=text("'affected'"),
    )
    declared_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.report_category.category_id")
    )
    location_id: Mapped[int] = mapped_column(
        ForeignKey("core.campus_location.location_id"), nullable=False
    )
    location_hint: Mapped[str | None] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    occurred_dow: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    is_emergency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_ongoing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reporter_contactable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    current_status: Mapped[ReportStatus] = mapped_column(
        pg_enum(ReportStatus, "report_status"), nullable=False, server_default=text("'submitted'")
    )
    current_risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    current_risk_band: Mapped[RiskBand | None] = mapped_column(pg_enum(RiskBand, "risk_band"))
    current_cluster_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_language: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'en'")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    category: Mapped[ReportCategory | None] = relationship(lazy="joined")
    location: Mapped[CampusLocation] = relationship(lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Report {self.public_ref} mode={self.submission_mode.value}>"


class ReportNarrative(Base):
    """``core.report_narrative`` — the narrative firewall.

    Split from ``core.report`` so read access can be revoked independently: the
    analytics role computes every hotspot and impact measurement without being
    able to read one student's account of what happened to them.

    Retention columns are stamped by a database trigger from ``core.system_policy``
    at insert.  The application does not supply them, and cannot alter them
    afterwards.
    """

    __tablename__ = "report_narrative"
    __table_args__ = {"schema": "core"}

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE"), primary_key=True
    )
    narrative: Mapped[str | None] = mapped_column(String)
    narrative_redacted: Mapped[str | None] = mapped_column(String)
    redaction_state: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'pending'")
    )
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    word_count: Mapped[int | None] = mapped_column(Integer)
    retention_policy_key: Mapped[str | None] = mapped_column(String)
    narrative_retention_days_applied: Mapped[int | None] = mapped_column(Integer)
    redacted_retention_days_applied: Mapped[int | None] = mapped_column(Integer)
    narrative_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redacted_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    narrative_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redacted_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_reason: Mapped[str | None] = mapped_column(String)

    @property
    def is_purged(self) -> bool:
        return self.narrative_purged_at is not None


class ReportAttribution(Base):
    """``identity.report_attribution`` — the only link between a report and a person.

    A row exists **if and only if** the reporter chose to be identified.  A
    database trigger refuses to create one for an anonymous report, so the
    guarantee does not depend on this application behaving correctly.

    Lives in the ``identity`` schema.  Repositories that serve student-facing
    report endpoints must not join through it except to answer "is this caller
    the reporter?", and must never return ``user_id`` outward.
    """

    __tablename__ = "report_attribution"
    __table_args__ = {"schema": "identity"}

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.app_user.user_id"), nullable=False
    )
    attributed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    contact_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    callback_contact: Mapped[str | None] = mapped_column(String)
    callback_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportAccessToken(Base):
    """``core.report_access_token`` — anonymous status lookup.

    Only the SHA-256 hash is stored; the raw token is shown once at submission
    and never persisted.  A database dump yields hashes.  This is the sole
    channel by which an anonymous reporter can follow their own case, because
    there is deliberately no identity to notify.
    """

    __tablename__ = "report_access_token"
    __table_args__ = {"schema": "core"}

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseStatusHistory(Base):
    """``core.case_status_history`` — append-only source of truth for status.

    A database trigger (``trg_sync_report_status``) copies every row inserted
    here onto ``core.report.current_status`` and ``closed_at``. The application
    never writes ``current_status`` directly — it writes a history row, and the
    cache follows. That ordering is not a style preference: an application that
    could set ``current_status`` without a history row would make "when did this
    change, by whom, and why" answerable only sometimes.
    """

    __tablename__ = "case_status_history"
    __table_args__ = {"schema": "core"}

    history_id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[ReportStatus | None] = mapped_column(pg_enum(ReportStatus, "report_status"))
    to_status: Mapped[ReportStatus] = mapped_column(
        pg_enum(ReportStatus, "report_status"), nullable=False
    )
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity.app_user.user_id"))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    remark: Mapped[str | None] = mapped_column(String)
    visible_to_reporter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    resolution_reason: Mapped[ResolutionReason | None] = mapped_column(
        pg_enum(ResolutionReason, "report_resolution_reason")
    )


class CaseAssignment(Base):
    """``core.case_assignment`` — which authority officer currently owns a report.

    **At most one active row per report**, enforced by a partial unique index
    (``uq_case_assignment_active``) rather than by application discipline: a
    reassignment must release the old row before — or atomically with — creating
    the new one, and the database refuses a state where that did not happen.

    ``assigned_to`` can never be a student — a trigger checks the assignee's role
    on every insert or update, independently of anything this application does.

    No column here can hold a reporter. Assignment is entirely a staff-facing
    concept: who owns the *response*, never who filed the report.
    """

    __tablename__ = "case_assignment"
    __table_args__ = {"schema": "core"}

    assignment_id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE"), nullable=False
    )
    assigned_to: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.app_user.user_id"), nullable=False
    )
    assigned_role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity.app_user.user_id"))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    assignment_note: Mapped[str | None] = mapped_column(String)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CaseAssignment report={self.report_id} active={self.is_active}>"


class SubmissionQuota(Base):
    """``identity.submission_quota`` — a counter, not a link.

    Records how many reports a user filed on a date, never which ones.  This is
    how anonymous submissions are rate-limited without reconstructing the
    connection that anonymity exists to prevent.
    """

    __tablename__ = "submission_quota"
    __table_args__ = {"schema": "identity"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.app_user.user_id", ondelete="CASCADE"), primary_key=True
    )
    quota_date: Mapped[date] = mapped_column(Date, primary_key=True)
    submitted_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
