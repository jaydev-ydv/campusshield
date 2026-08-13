"""Related-report detection.

`find_related(report, corpus)` proposes reports describing the same or a related
event. The backend will call this later; for now it runs offline against a
prepared corpus, and the interface is the one the backend will use.

**What the similarity may see, and what it may not.**

Permitted: the narrative text, the controlled location, the timestamp, and the
category. All of those describe the *event*.

Forbidden, and absent from the `SimilarityCandidate` type entirely rather than
merely unused: reporter identity, `reporter_relationship`, and anything
credibility-shaped. Two anonymous reports being linked does **not** imply the same
reporter, and `reporter_relationship` records a vantage point, not reliability —
`core.risk_assessment` already rejects it with a CHECK constraint, and the same
rule holds here. There is no field to reach for.

**Duplicate versus related.** A duplicate is the same event reported twice: high
text similarity *and* the same location *and* close in time. A related report is
the same pattern recurring: high text similarity, but possibly weeks apart. The
time bound is what separates them, and getting it wrong collapses a recurring
hazard into a single "duplicate" and hides the pattern the system exists to find.

**No accuracy is claimed.** There is no labelled set of genuinely duplicate
reports, so this ships as a tested implementation with a defined evaluation
strategy (§ `evaluation_strategy`), not as a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .preprocess import preprocess


@dataclass(frozen=True, slots=True)
class SimilarityCandidate:
    """A report as the similarity layer is allowed to see it.

    Note what has no field here: no user id, no reporter_relationship, no
    credibility score. The type makes the prohibition structural rather than a
    convention someone has to remember.
    """

    report_id: str
    text: str
    location_id: int | None = None
    category_code: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RelatedReport:
    report_id: str
    similarity: float
    relationship: str  # 'duplicate' | 'related'
    rationale: str


class Embedder(Protocol):
    """Turns texts into vectors.

    TF-IDF in the prototype; sentence embeddings in production. The interface is
    the same, so swapping one for the other does not touch `find_related`.
    """

    def fit(self, texts: list[str]) -> "Embedder": ...
    def transform(self, texts: list[str]) -> np.ndarray: ...


class TfidfEmbedder:
    """TF-IDF vectors, L2-normalised so cosine similarity is a dot product."""

    def __init__(self, **kwargs: Any) -> None:
        self._vectorizer = TfidfVectorizer(
            ngram_range=kwargs.get("ngram_range", (1, 2)),
            min_df=kwargs.get("min_df", 1),
            sublinear_tf=True,
            lowercase=False,
        )
        self._fitted = False

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        self._vectorizer.fit([preprocess(t) for t in texts])
        self._fitted = True
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("embedder must be fitted before transform")
        matrix = self._vectorizer.transform([preprocess(t) for t in texts]).toarray()
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        # Zero vectors stay zero rather than becoming NaN: a report whose every
        # token is out of vocabulary must score 0 similarity, not crash.
        norms[norms == 0] = 1.0
        return matrix / norms


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity of one vector against many, both pre-normalised."""
    return np.clip(b @ a, -1.0, 1.0)


def find_related(
    report: SimilarityCandidate,
    corpus: list[SimilarityCandidate],
    *,
    embedder: Embedder | None = None,
    related_threshold: float = 0.72,
    duplicate_threshold: float = 0.88,
    top_k: int = 5,
    duplicate_max_days_apart: int = 14,
) -> list[RelatedReport]:
    """Find reports related to `report`, most similar first.

    The report itself is excluded from its own results. Returns at most `top_k`.
    """
    if duplicate_threshold < related_threshold:
        raise ValueError("duplicate_threshold must be >= related_threshold")

    others = [c for c in corpus if c.report_id != report.report_id]
    if not others:
        return []

    if embedder is None:
        embedder = TfidfEmbedder().fit([c.text for c in [report, *others]])

    vectors = embedder.transform([c.text for c in others])
    query = embedder.transform([report.text])[0]
    scores = cosine_similarity(query, vectors)

    results: list[RelatedReport] = []
    for candidate, score in zip(others, scores, strict=True):
        similarity = float(score)
        if similarity < related_threshold:
            continue

        relationship, rationale = _classify_relationship(
            report,
            candidate,
            similarity,
            duplicate_threshold=duplicate_threshold,
            duplicate_max_days_apart=duplicate_max_days_apart,
        )
        results.append(
            RelatedReport(
                report_id=candidate.report_id,
                similarity=round(similarity, 4),
                relationship=relationship,
                rationale=rationale,
            )
        )

    results.sort(key=lambda r: r.similarity, reverse=True)
    return results[:top_k]


def _classify_relationship(
    report: SimilarityCandidate,
    candidate: SimilarityCandidate,
    similarity: float,
    *,
    duplicate_threshold: float,
    duplicate_max_days_apart: int,
) -> tuple[str, str]:
    """Duplicate or related, and why.

    A duplicate needs three things to agree: text, place, and time. Text
    similarity alone is not enough — the same hazard reported at two different
    gates is two real problems, and merging them would hide one.
    """
    if similarity < duplicate_threshold:
        return "related", f"similar wording (cosine {similarity:.2f})"

    if report.location_id is not None and candidate.location_id is not None:
        if report.location_id != candidate.location_id:
            return (
                "related",
                f"similar wording (cosine {similarity:.2f}) but a different location — "
                "likely a recurring pattern rather than the same event",
            )

    if report.occurred_at and candidate.occurred_at:
        gap = abs(report.occurred_at - candidate.occurred_at)
        if gap > timedelta(days=duplicate_max_days_apart):
            return (
                "related",
                f"similar wording (cosine {similarity:.2f}) but {gap.days} days apart — "
                "a recurrence, not a duplicate",
            )

    return "duplicate", f"very similar wording (cosine {similarity:.2f}), same place and time window"


def evaluation_strategy() -> dict[str, Any]:
    """How this would be evaluated once labelled pairs exist.

    Recorded in the results file so the absence of a number is explicit rather
    than an omission a reader has to notice.
    """
    return {
        "status": "not_evaluated",
        "reason": (
            "No labelled set of genuinely duplicate or related report pairs exists. "
            "Reporting a precision or recall figure without one would be fabrication."
        ),
        "planned_method": {
            "annotation": (
                "Two annotators independently label a sample of candidate pairs as "
                "duplicate / related / unrelated, with Cohen's kappa reported for agreement."
            ),
            "sampling": (
                "Pairs drawn stratified across the similarity range, not only the top "
                "scores — sampling only high-similarity pairs measures precision and is "
                "blind to everything the system missed."
            ),
            "metrics": [
                "precision@k for the top-k returned",
                "recall against the annotated duplicate set",
                "a threshold sweep, since duplicate_threshold and related_threshold "
                "are the two parameters that actually decide behaviour",
            ],
            "operating_point": (
                "Favour recall over precision. A missed duplicate hides a pattern; a "
                "false duplicate is a link an authority can dismiss in a moment. The "
                "two errors do not cost the same."
            ),
        },
    }
