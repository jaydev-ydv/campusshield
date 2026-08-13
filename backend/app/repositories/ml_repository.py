"""Data access for the ML layer.

Two things this module deliberately cannot do.

**It cannot reach a reporter.** No query here touches
`identity.report_attribution`. Classification, similarity and risk scoring have
no legitimate use for who filed a report, so they are not given the ability to
ask — the same structural approach `IncidentRepository` takes.

**It cannot return an embedding to a caller.** `embeddings_for_model` is used by
the similarity comparison inside the service and nothing else; no serializer sees
a vector. Raw TF-IDF weights plus a vocabulary are close enough to a bag of words
that they should be treated as narrative-adjacent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..models import (
    CampusLocation,
    ModelVersion,
    Report,
    ReportCategory,
    ReportClassification,
    ReportEmbedding,
    ReportLink,
    RiskAssessment,
)
from ..models.enums import LinkReview, LinkType, MlTask, ReportStatus, RiskBand

CLOSED_STATUSES = (
    ReportStatus.RESOLVED,
    ReportStatus.CLOSED_NO_ACTION,
    ReportStatus.WITHDRAWN,
    ReportStatus.DUPLICATE,
)


@dataclass(frozen=True, slots=True)
class LinkedReport:
    """One side of a link, with just enough to render it.

    No narrative, no reporter, no evidence. A responder decides whether to open
    the other report from this; they do not read it from here.
    """

    link_id: int
    other_report_id: uuid.UUID
    public_ref: str
    link_type: LinkType
    similarity: Decimal | None
    review_state: LinkReview
    occurred_at: datetime
    location_name: str
    category_label: str | None


class MlRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- model registry ----------------------------------------------------

    def active_classifier(self) -> ModelVersion | None:
        return self._session.scalar(
            select(ModelVersion).where(
                ModelVersion.task == MlTask.CLASSIFICATION,
                ModelVersion.is_active.is_(True),
            )
        )

    def get_model(self, model_id: uuid.UUID) -> ModelVersion | None:
        return self._session.get(ModelVersion, model_id)

    def add_model(self, model: ModelVersion) -> ModelVersion:
        self._session.add(model)
        self._session.flush()
        return model

    # -- categories --------------------------------------------------------

    def category_by_code(self, code: str) -> ReportCategory | None:
        return self._session.scalar(select(ReportCategory).where(ReportCategory.code == code))

    def category_by_id(self, category_id: int) -> ReportCategory | None:
        return self._session.get(ReportCategory, category_id)

    def get_report(self, report_id: uuid.UUID) -> Report | None:
        return self._session.get(Report, report_id)

    # -- classification ----------------------------------------------------

    def record_classification(
        self,
        report_id: uuid.UUID,
        *,
        model_id: uuid.UUID,
        predicted_category_id: int | None,
        confidence: float | None,
        label_scores: dict[str, float],
        latency_ms: int | None,
    ) -> ReportClassification:
        """Insert and make current.

        Older rows are demoted rather than deleted: re-running a newer model over
        old reports must not erase what the system actually suggested at the time
        a responder was looking at it.
        """
        self._session.execute(
            update(ReportClassification)
            .where(
                ReportClassification.report_id == report_id,
                ReportClassification.is_current.is_(True),
            )
            .values(is_current=False)
        )
        row = ReportClassification(
            report_id=report_id,
            model_id=model_id,
            predicted_category_id=predicted_category_id,
            confidence=confidence,
            label_scores=label_scores,
            latency_ms=latency_ms,
            is_current=True,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def current_classification(self, report_id: uuid.UUID) -> ReportClassification | None:
        return self._session.scalar(
            select(ReportClassification).where(
                ReportClassification.report_id == report_id,
                ReportClassification.is_current.is_(True),
            )
        )

    def record_override(
        self, classification: ReportClassification, *, category_id: int, user_id: uuid.UUID
    ) -> ReportClassification:
        """A human disagreeing with the suggestion.

        The three override columns move together — a CHECK requires it — so a
        half-recorded override cannot exist. This does **not** change
        `core.report.declared_category_id`: what the student chose stays on the
        report, and the responder's judgement is recorded beside it.
        """
        classification.overridden_category_id = category_id
        classification.overridden_by = user_id
        classification.overridden_at = datetime.now(timezone.utc)
        self._session.flush()
        return classification

    # -- embeddings --------------------------------------------------------

    def record_embedding(
        self, report_id: uuid.UUID, model_id: uuid.UUID, vector: list[float], norm: float
    ) -> ReportEmbedding:
        row = ReportEmbedding(
            report_id=report_id,
            model_id=model_id,
            embedding=vector,
            dim=len(vector),
            l2_norm=norm,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def embeddings_for_model(
        self, model_id: uuid.UUID, *, exclude_report_id: uuid.UUID, limit: int = 5000
    ) -> list[tuple[uuid.UUID, list[float], float]]:
        """Vectors to compare against.

        Restricted to reports still open: a resolved case from last year is not
        something a responder needs proposed as related to what just arrived, and
        scanning them costs time for nothing.
        """
        rows = self._session.execute(
            select(ReportEmbedding.report_id, ReportEmbedding.embedding, ReportEmbedding.l2_norm)
            .join(Report, Report.report_id == ReportEmbedding.report_id)
            .where(
                ReportEmbedding.model_id == model_id,
                ReportEmbedding.report_id != exclude_report_id,
                Report.current_status.not_in(CLOSED_STATUSES),
            )
            .limit(limit)
        ).all()
        return [(row[0], list(row[1]), float(row[2])) for row in rows]

    # -- links -------------------------------------------------------------

    def record_link(
        self,
        report_id: uuid.UUID,
        other_id: uuid.UUID,
        *,
        link_type: LinkType,
        similarity: float,
        method: str,
        model_id: uuid.UUID | None,
    ) -> ReportLink | None:
        """Propose a link, once.

        `ck_report_link_canonical_order` requires `report_id_a < report_id_b`, so
        the pair is ordered here rather than relying on the caller. That ordering
        is also what makes "does this pair already exist?" a single lookup.
        """
        first, second = sorted([report_id, other_id], key=str)
        if first == second:
            return None

        existing = self._session.scalar(
            select(ReportLink).where(
                ReportLink.report_id_a == first, ReportLink.report_id_b == second
            )
        )
        if existing is not None:
            # Never overwrite a human's decision with a fresh guess.
            return existing

        link = ReportLink(
            report_id_a=first,
            report_id_b=second,
            link_type=link_type,
            similarity=similarity,
            method=method,
            model_id=model_id,
            review_state=LinkReview.UNREVIEWED,
        )
        self._session.add(link)
        self._session.flush()
        return link

    def related_for(self, report_id: uuid.UUID) -> list[LinkedReport]:
        """Links touching this report, from its point of view.

        Rejected links are excluded: a human has already said these are not
        related, and showing them again wastes the next responder's attention.
        """
        other_id = func.coalesce(
            func.nullif(ReportLink.report_id_a, report_id), ReportLink.report_id_b
        )
        rows = self._session.execute(
            select(
                ReportLink.link_id,
                other_id.label("other_report_id"),
                Report.public_ref,
                ReportLink.link_type,
                ReportLink.similarity,
                ReportLink.review_state,
                Report.occurred_at,
                CampusLocation.name,
                ReportCategory.label,
            )
            .join(Report, Report.report_id == other_id)
            .join(CampusLocation, CampusLocation.location_id == Report.location_id)
            .outerjoin(ReportCategory, ReportCategory.category_id == Report.declared_category_id)
            .where(
                or_(ReportLink.report_id_a == report_id, ReportLink.report_id_b == report_id),
                ReportLink.review_state != LinkReview.REJECTED,
            )
            .order_by(ReportLink.similarity.desc())
        ).all()

        return [
            LinkedReport(
                link_id=row[0],
                other_report_id=row[1],
                public_ref=row[2],
                link_type=row[3],
                similarity=row[4],
                review_state=row[5],
                occurred_at=row[6],
                location_name=row[7],
                category_label=row[8],
            )
            for row in rows
        ]

    def get_link(self, link_id: int) -> ReportLink | None:
        return self._session.get(ReportLink, link_id)

    def review_link(self, link: ReportLink, *, state: LinkReview, user_id: uuid.UUID) -> ReportLink:
        link.review_state = state
        link.reviewed_by = user_id
        self._session.flush()
        return link

    # -- risk --------------------------------------------------------------

    def record_risk(
        self,
        report_id: uuid.UUID,
        *,
        score: float,
        band: RiskBand,
        scorer_version: str,
        factors: dict[str, Any],
        trigger_reason: str,
    ) -> RiskAssessment:
        self._session.execute(
            update(RiskAssessment)
            .where(RiskAssessment.report_id == report_id, RiskAssessment.is_current.is_(True))
            .values(is_current=False)
        )
        assessment = RiskAssessment(
            report_id=report_id,
            score=Decimal(str(score)),
            band=band,
            scorer_version=scorer_version,
            factors=factors,
            trigger_reason=trigger_reason,
            is_current=True,
        )
        self._session.add(assessment)
        self._session.flush()
        return assessment

    def current_risk(self, report_id: uuid.UUID) -> RiskAssessment | None:
        return self._session.scalar(
            select(RiskAssessment).where(
                RiskAssessment.report_id == report_id, RiskAssessment.is_current.is_(True)
            )
        )

    def count_recent_open_at_location(
        self, location_id: int, *, since: datetime, exclude_report_id: uuid.UUID
    ) -> int:
        """Other open reports at the same place, recently.

        A property of a location, not of a person. Counting reports by the same
        reporter would be a repeat-complainer signal, which is precisely what
        `ck_risk_factors_no_credibility_terms` exists to prevent — and this query
        has no way to express it.
        """
        return (
            self._session.scalar(
                select(func.count())
                .select_from(Report)
                .where(
                    Report.location_id == location_id,
                    Report.report_id != exclude_report_id,
                    Report.submitted_at >= since,
                    Report.current_status.not_in(CLOSED_STATUSES),
                )
            )
            or 0
        )
