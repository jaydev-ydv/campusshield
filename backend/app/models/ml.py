"""Mapping for the ML tables and the two core tables they write.

Created by migration `0001`; mapped here because Phase 4C is the first thing to
write them.

Two properties of this layer are worth stating before the code.

**The student's declared category is authoritative.** `ml.report_classification`
holds a *suggestion*. `core.report.declared_category_id` is what the student
chose and is never overwritten by a model — exactly as `core.report.location_id`
is never overwritten by a photograph's GPS. A responder can record a different
judgement, and that is stored as an override on the classification row, by a
named human, with a timestamp. The report's own category changes only through the
case workflow, never through inference.

**Risk factors cannot contain a credibility term.** `core.risk_assessment` has a
CHECK constraint refusing `reporter_relationship`, `credibility`, `trust`,
`reliability` and friends at both the top level and inside `weights`. The
application does not supply them; the database would reject the row if it did.
That constraint is the reason this table can be trusted not to become a
reporter-scoring system by accretion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base
from .enums import LinkReview, LinkType, MlTask, ModelFamily, RiskBand, pg_enum


class ModelVersion(Base):
    """``ml.model_version`` — the registry of models that have been served.

    A row is never deleted and `artifact_uri` is never rewritten, so a
    classification from six months ago can still be traced to the exact model
    that produced it. `headline_metrics` carries `data_provenance`, which is how
    a query against this table alone answers "was this trained on real data?".
    """

    __tablename__ = "model_version"
    __table_args__ = {"schema": "ml"}

    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    task: Mapped[MlTask] = mapped_column(pg_enum(MlTask, "ml_task"), nullable=False)
    family: Mapped[ModelFamily] = mapped_column(
        pg_enum(ModelFamily, "model_family"), nullable=False
    )
    version: Mapped[str] = mapped_column(String, nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(String)
    embedding_dim: Mapped[int | None] = mapped_column(SmallInteger)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_rows: Mapped[int | None] = mapped_column(Integer)
    hyperparameters: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    headline_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    @property
    def is_real_world_trained(self) -> bool:
        provenance = (self.headline_metrics or {}).get("data_provenance") or {}
        return bool(provenance.get("is_real_world_data", False))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModelVersion {self.name}@{self.version}>"


class ReportClassification(Base):
    """``ml.report_classification`` — what a model suggested for one report.

    `predicted_category_id` is a suggestion and nothing else. The override
    columns record a human disagreeing with it: who, what they chose instead, and
    when. Those three are constrained to move together, so a half-recorded
    override cannot exist.
    """

    __tablename__ = "report_classification"
    __table_args__ = {"schema": "ml"}

    classification_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), nullable=False
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ml.model_version.model_id"), nullable=False
    )
    predicted_category_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("core.report_category.category_id")
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    label_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    inferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    overridden_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity.app_user.user_id")
    )
    overridden_category_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("core.report_category.category_id")
    )
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportEmbedding(Base):
    """``ml.report_embedding`` — the vector used to find related reports.

    Stored as `REAL[]` rather than a pgvector column: `DATABASE.md` rules out
    extensions beyond `pgcrypto`, and at campus scale an exact scan over a few
    thousand rows is faster than the index would save.

    **This is derived from the narrative and is therefore narrative-adjacent.**
    It is never returned by any endpoint. A sufficiently motivated holder of raw
    TF-IDF weights plus the vocabulary could recover a bag of words from a
    student's account, so the vectors stay server-side.
    """

    __tablename__ = "report_embedding"
    __table_args__ = {"schema": "ml"}

    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), primary_key=True
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ml.model_version.model_id"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    l2_norm: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ReportLink(Base):
    """``core.report_link`` — two reports that may describe the same thing.

    Proposed by similarity, confirmed or rejected by a human: `review_state`
    starts `unreviewed` and a CHECK requires a reviewer once it leaves that
    state. Nothing acts on an unreviewed link.

    **A link never widens access.** It is metadata about a pair, not a grant over
    either one. The service only shows a responder a link whose other side they
    could already open on their own.
    """

    __tablename__ = "report_link"
    __table_args__ = {"schema": "core"}

    link_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id_a: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), nullable=False
    )
    report_id_b: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), nullable=False
    )
    link_type: Mapped[LinkType] = mapped_column(pg_enum(LinkType, "link_type"), nullable=False)
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    method: Mapped[str | None] = mapped_column(String)
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ml.model_version.model_id")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    review_state: Mapped[LinkReview] = mapped_column(
        pg_enum(LinkReview, "link_review"), nullable=False, server_default=text("'unreviewed'")
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity.app_user.user_id")
    )


class RiskAssessment(Base):
    """``core.risk_assessment`` — a triage prompt, not a verdict.

    `factors` is required to be a JSON object and is CHECK-barred from containing
    any credibility term. The scorer reads the incident: what category, whether
    it was raised as an emergency, whether it is ongoing, when it happened, and
    how many other reports point at the same place. It cannot read who filed it —
    `TriageService` is not given a reporter to read.
    """

    __tablename__ = "risk_assessment"
    __table_args__ = {"schema": "core"}

    assessment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.report.report_id"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    band: Mapped[RiskBand] = mapped_column(pg_enum(RiskBand, "risk_band"), nullable=False)
    scorer_version: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ml.model_version.model_id")
    )
    factors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    trigger_reason: Mapped[str | None] = mapped_column(String)
