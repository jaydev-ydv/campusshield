"""Machine assistance at submission: suggest, relate, and prompt — never decide.

## The three outputs, and what each is worth

**A category suggestion.** The model reads the narrative and proposes one of the
sixteen controlled categories. `core.report.declared_category_id` — what the
student chose — is never changed by it. This mirrors the location trust model
from 4B-2 exactly: the person's deliberate choice is authoritative, and the
derived signal describes rather than overrides.

**Related reports.** Cosine similarity over TF-IDF vectors proposes that two
reports may describe the same event or the same recurring problem. Proposals
enter `core.report_link` as `unreviewed` and nothing acts on them until a human
confirms.

**A risk band.** Rule-based triage ordering. See `risk_scorer.py`.

## Everything here is best-effort

`run_for_report` catches its own failures and returns. **A student's report must
never be lost because a model failed to load, a vector was malformed, or the
artifact was missing.** The report is the product; this is assistance. A failure
is logged and the report stands without a suggestion, which is exactly the state
every report was in before this phase.

## The anonymity rule for links

A link between an anonymous report and an identified one is legitimate — two
people can report the same incident — but it must never become an identity
bridge. Two things keep it from becoming one:

1. **A link never widens access.** `related_for` returns only links whose *other*
   side the caller could already open on their own. A responder who cannot see
   report B learns nothing about it from report A.
2. **A link carries no identity.** It is a reference, a type, and a similarity.
   Resolving who filed either side is not something this module can do; it never
   queries `identity.report_attribution`.

## Anonymous submissions are not treated differently by the model

The classifier sees a narrative. It is not told the submission mode, the
reporter, or the relationship — so an anonymous report cannot be scored, ranked,
or suggested differently from an identified one.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..errors import NotFoundError, ValidationError
from ..ml.artifact import ArtifactError, ModelBundle
from ..models import Report, ReportCategory
from ..models.enums import LinkReview, LinkType
from ..repositories.ml_repository import MlRepository
from .risk_scorer import score_report

logger = logging.getLogger(__name__)

# Above this cosine similarity, two reports are proposed as the same event.
DUPLICATE_THRESHOLD = 0.88
# Above this, they are proposed as related. Both mirror ml/configs/default.yaml.
RELATED_THRESHOLD = 0.72
# Reports further apart than this are never proposed as duplicates however
# similar the text: the same hazard in March and October is a recurrence, not one
# event reported twice.
DUPLICATE_MAX_DAYS_APART = 14
# Most similar candidates considered. A responder cannot act on twenty links.
TOP_K = 5
# Recent reports at the same location that feed the risk scorer's repeat factor.
LOCATION_REPEAT_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class RelatedReport:
    """One proposed link, from the point of view of the report being viewed."""

    public_ref: str
    link_type: LinkType
    similarity: float
    review_state: str
    link_id: int
    occurred_at: datetime
    location_name: str
    category_label: str | None


@dataclass(frozen=True, slots=True)
class TriageView:
    """What a responder is shown. Assembled by :meth:`TriageService.view_for`."""

    suggested_category: ReportCategory | None
    suggested_confidence: float | None
    declared_matches_suggestion: bool | None
    model_name: str | None
    model_is_real_world_trained: bool
    overridden_category: ReportCategory | None
    risk_score: float | None
    risk_band: str | None
    risk_factors: dict[str, Any] | None
    related: list[RelatedReport]


class TriageService:
    def __init__(
        self,
        *,
        ml: MlRepository,
        campus_timezone: str,
        bundle: ModelBundle | None = None,
    ) -> None:
        self._ml = ml
        self._campus_timezone = campus_timezone
        self._bundle = bundle

    # -- write path --------------------------------------------------------

    def run_for_report(
        self, report: Report, narrative: str, category: ReportCategory | None
    ) -> None:
        """Classify, embed, relate, and score. Never raises.

        Runs inside the submission transaction. Every failure mode ends the same
        way: a log line and a report without a suggestion.
        """
        try:
            self._run(report, narrative, category)
        except ArtifactError as exc:
            logger.warning("triage skipped, no usable model: %s", exc)
        except Exception:
            # Deliberately broad. The alternative is a student being told their
            # report failed because a matrix was the wrong shape.
            logger.exception("triage failed for report %s; the report stands", report.public_ref)

    def _run(self, report: Report, narrative: str, category: ReportCategory | None) -> None:
        now = datetime.now(timezone.utc)
        model = self._ml.active_classifier()

        if model is not None and self._bundle is not None and narrative:
            self._classify_and_embed(report, narrative, model.model_id, now)

        # Risk is scored whether or not a model was available: it reads the
        # incident, not the narrative, so it does not depend on one.
        repeat_count = self._ml.count_recent_open_at_location(
            report.location_id,
            since=now - timedelta(days=LOCATION_REPEAT_WINDOW_DAYS),
            exclude_report_id=report.report_id,
        )
        result = score_report(
            report,
            category,
            campus_timezone=self._campus_timezone,
            location_repeat_count=repeat_count,
            now=now,
        )
        self._ml.record_risk(
            report.report_id,
            score=result.score,
            band=result.band,
            scorer_version=result.scorer_version,
            factors=result.factors,
            trigger_reason="initial",
        )

    def _classify_and_embed(
        self, report: Report, narrative: str, model_id: uuid.UUID, now: datetime
    ) -> None:
        assert self._bundle is not None
        estimator = self._bundle.estimator

        started = time.perf_counter()
        probabilities = estimator.predict_proba([narrative])[0]
        latency_ms = round((time.perf_counter() - started) * 1000)

        classes = list(estimator.classes_)
        scores = {code: round(float(p), 4) for code, p in zip(classes, probabilities, strict=True)}
        best_code = max(scores, key=lambda code: scores[code])

        predicted = self._ml.category_by_code(best_code)
        self._ml.record_classification(
            report.report_id,
            model_id=model_id,
            predicted_category_id=predicted.category_id if predicted else None,
            confidence=scores[best_code],
            # The full distribution, not just the winner. A responder deciding
            # whether to trust a 0.34 suggestion needs to see that the runner-up
            # was 0.31.
            label_scores=scores,
            latency_ms=latency_ms,
        )

        vector = self._vectorise(narrative)
        if vector is None:
            return
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            # A narrative made entirely of out-of-vocabulary words. Nothing to
            # compare, and the CHECK requires a positive norm.
            return
        self._ml.record_embedding(report.report_id, model_id, vector, norm)
        self._propose_links(report, model_id, vector, norm, now)

    def _vectorise(self, narrative: str) -> list[float] | None:
        """The TF-IDF vector for a narrative, from the served pipeline.

        Reuses the classifier's own vectoriser so links and suggestions live in
        the same space, and so there is no second model to keep in step.
        """
        assert self._bundle is not None
        pipeline = self._bundle.estimator
        try:
            normalised = pipeline.named_steps["normalise"].transform([narrative])
            matrix = pipeline.named_steps["tfidf"].transform(normalised)
        except (KeyError, AttributeError):
            logger.info("served model exposes no tfidf step; embeddings skipped")
            return None
        return [float(value) for value in matrix.toarray()[0]]

    def _propose_links(
        self,
        report: Report,
        model_id: uuid.UUID,
        vector: list[float],
        norm: float,
        now: datetime,
    ) -> None:
        """Compare against existing embeddings and propose links.

        An exact scan. At campus scale — a few thousand reports a year — this is
        milliseconds, and `DATABASE.md` rules out pgvector. When it stops being
        fast enough, the index goes here rather than the architecture changing.
        """
        candidates = self._ml.embeddings_for_model(model_id, exclude_report_id=report.report_id)
        if not candidates:
            return

        scored: list[tuple[float, uuid.UUID]] = []
        for other_id, other_vector, other_norm in candidates:
            if len(other_vector) != len(vector) or other_norm <= 0:
                continue
            dot = sum(a * b for a, b in zip(vector, other_vector, strict=True))
            similarity = dot / (norm * other_norm)
            if similarity >= RELATED_THRESHOLD:
                scored.append((similarity, other_id))

        scored.sort(reverse=True, key=lambda pair: pair[0])
        for similarity, other_id in scored[:TOP_K]:
            other = self._ml.get_report(other_id)
            if other is None:
                continue

            link_type = LinkType.RELATED
            if similarity >= DUPLICATE_THRESHOLD:
                apart = abs((report.occurred_at - other.occurred_at).days)
                # Close in text and close in time: one event. Close in text but
                # months apart: a recurring problem, which is a different and
                # more useful thing to know.
                link_type = (
                    LinkType.DUPLICATE
                    if apart <= DUPLICATE_MAX_DAYS_APART
                    else LinkType.SAME_PATTERN
                )

            self._ml.record_link(
                report.report_id,
                other_id,
                link_type=link_type,
                similarity=round(min(1.0, similarity), 4),
                method="embedding_cosine",
                model_id=model_id,
            )

    # -- correction by a human ---------------------------------------------

    def override_category(self, report: Report, category_id: int, user_id: uuid.UUID) -> None:
        classification = self._ml.current_classification(report.report_id)
        if classification is None:
            raise NotFoundError("This report has no category suggestion to correct.")
        # Range-checked before the lookup. `core.report_category.category_id` is
        # a smallint, and a larger value reaches the database as an overflow —
        # which surfaces as a 503 rather than "no such category", telling the
        # caller the service is broken when their input was simply wrong.
        if not (0 < category_id <= 32767):
            raise ValidationError(
                "Unknown or inactive report category.",
                details={"fields": {"category_id": ["No such active category."]}},
            )
        category = self._ml.category_by_id(category_id)
        if category is None or not category.is_active:
            raise ValidationError(
                "Unknown or inactive report category.",
                details={"fields": {"category_id": ["No such active category."]}},
            )
        self._ml.record_override(classification, category_id=category_id, user_id=user_id)

    def review_link(
        self,
        report: Report,
        link_id: int,
        confirmed: bool,
        user_id: uuid.UUID,
        *,
        visible: set[uuid.UUID],
    ) -> None:
        link = self._ml.get_link(link_id)
        if link is None:
            raise NotFoundError("No such link.")

        sides = {link.report_id_a, link.report_id_b}
        if report.report_id not in sides:
            raise NotFoundError("No such link.")

        # Both sides, not just this one. Reviewing a link to a report the caller
        # cannot open would let them assert a relationship to something they are
        # not entitled to know about.
        other = (sides - {report.report_id}).pop()
        if other not in visible:
            raise NotFoundError("No such link.")

        self._ml.review_link(
            link,
            state=LinkReview.CONFIRMED if confirmed else LinkReview.REJECTED,
            user_id=user_id,
        )

    # -- read path ---------------------------------------------------------

    def view_for(
        self,
        report: Report,
        *,
        visible: set[uuid.UUID] | None = None,
    ) -> TriageView:
        """Assemble what a responder sees.

        `visible` is the set of report ids the caller may already open. Links to
        anything outside it are dropped — a link is metadata about a pair, never
        a grant over either side.
        """
        classification = self._ml.current_classification(report.report_id)
        risk = self._ml.current_risk(report.report_id)

        suggested = overridden = None
        confidence = None
        model_name = None
        real_world = False
        if classification is not None:
            if classification.predicted_category_id is not None:
                suggested = self._ml.category_by_id(classification.predicted_category_id)
            if classification.overridden_category_id is not None:
                overridden = self._ml.category_by_id(classification.overridden_category_id)
            confidence = (
                float(classification.confidence) if classification.confidence is not None else None
            )
            model = self._ml.get_model(classification.model_id)
            if model is not None:
                model_name = f"{model.name}@{model.version}"
                real_world = model.is_real_world_trained

        matches = (
            None
            if suggested is None or report.declared_category_id is None
            else suggested.category_id == report.declared_category_id
        )

        related = [
            link
            for link in self._ml.related_for(report.report_id)
            if visible is None or link.other_report_id in visible
        ]

        return TriageView(
            suggested_category=suggested,
            suggested_confidence=confidence,
            declared_matches_suggestion=matches,
            model_name=model_name,
            model_is_real_world_trained=real_world,
            overridden_category=overridden,
            risk_score=float(risk.score) if risk else None,
            risk_band=risk.band.value if risk else None,
            risk_factors=risk.factors if risk else None,
            related=[
                RelatedReport(
                    public_ref=link.public_ref,
                    link_type=link.link_type,
                    similarity=float(link.similarity) if link.similarity is not None else 0.0,
                    review_state=link.review_state.value,
                    link_id=link.link_id,
                    occurred_at=link.occurred_at,
                    location_name=link.location_name,
                    category_label=link.category_label,
                )
                for link in related
            ],
        )
