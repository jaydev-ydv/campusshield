"""The served model: artifact contract, train/serve equivalence, and the scorer.

No mocked estimator anywhere. These load the real exported artifact and call the
real scikit-learn pipeline — a test that stubbed the model would pass whether or
not the thing actually classifies anything.

The artifact is a build output rather than a source file, so tests that need it
skip when it is absent and say how to produce one.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ml.artifact import ArtifactError, ModelBundle, load_bundle, save_bundle
from app.ml.text import TextNormalizer, normalise
from app.models import Report, ReportCategory
from app.models.enums import ReportKind, RiskBand, SubmissionMode, UserRole
from app.services.risk_scorer import (
    BARRED_FACTOR_KEYS,
    SCORER_VERSION,
    band_for,
    score_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = PROJECT_ROOT / "ml" / "artifacts" / "tfidf-logreg-1.0.0.joblib"

needs_artifact = pytest.mark.skipif(
    not ARTIFACT.exists(),
    reason="no model artifact; run scripts/export_baseline_model.py",
)


@pytest.fixture(scope="module")
def bundle() -> ModelBundle:
    if not ARTIFACT.exists():
        pytest.skip("no model artifact")
    return load_bundle(ARTIFACT)


# ---------------------------------------------------------------------------
# Train/serve equivalence
# ---------------------------------------------------------------------------


def test_backend_normalisation_matches_the_research_pipeline():
    """The one that prevents silent model decay.

    `app/ml/text.py` and `ml/src/preprocess.py` must agree exactly. If they
    drift, the vectoriser starts seeing text that does not look like what it was
    fitted on, and nothing fails — the model just quietly gets worse.
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from ml.src.preprocess import preprocess
    from ml.src.synthetic import generate

    corpus = generate(examples_per_category=8, seed=99, cross_category_noise=0.25)
    assert corpus, "generator produced nothing to compare"

    for example in corpus:
        assert normalise(example.text) == preprocess(example.text), example.text

    # And the awkward inputs a generator will not produce.
    for text in [
        "",
        "   ",
        "Visit https://example.com/report now",
        "CAPS and    irregular\n\nwhitespace",
        "www.example.org followed me",
        "digits 12345 stay by default",
    ]:
        assert normalise(text) == preprocess(text), repr(text)


def test_the_normalizer_survives_a_round_trip_through_pickle():
    """It is pickled inside the artifact, so its options must travel with it."""
    import pickle

    original = TextNormalizer(lowercase=False, strip_digits=True)
    restored = pickle.loads(pickle.dumps(original))
    assert restored.lowercase is False
    assert restored.strip_digits is True
    assert restored.transform(["Room 214 Was LOUD"]) == original.transform(["Room 214 Was LOUD"])


def test_normalise_handles_none():
    assert normalise(None) == ""


# ---------------------------------------------------------------------------
# The artifact contract
# ---------------------------------------------------------------------------


@needs_artifact
def test_the_artifact_loads_and_describes_itself(bundle: ModelBundle):
    assert bundle.name
    assert bundle.version
    assert len(bundle.classes) == 16
    assert bundle.headline_metrics["macro_f1"] > 0


@needs_artifact
def test_the_artifact_declares_synthetic_provenance(bundle: ModelBundle):
    """The claim the whole system must keep making.

    Every model this project can produce is trained on generated text. If this
    ever flips to True it must be because real data was legitimately obtained and
    `ml/DATASET.md` says so — not because a default changed.
    """
    assert bundle.provenance["is_real_world_data"] is False
    assert bundle.provenance["provenance"] == "synthetic"
    assert bundle.is_real_world_trained is False


@needs_artifact
def test_the_pipeline_takes_a_raw_narrative(bundle: ModelBundle):
    """Normalisation is inside the estimator, so there is no second code path."""
    probabilities = bundle.estimator.predict_proba(
        ["   Someone FOLLOWED me from https://x.test the library   "]
    )[0]
    assert len(probabilities) == 16
    assert abs(sum(probabilities) - 1.0) < 1e-6


@needs_artifact
def test_predicted_codes_are_real_report_categories(bundle: ModelBundle):
    """A model cannot predict something a student could not have chosen."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from ml.src.labels import CATEGORY_CODES

    assert set(bundle.classes) == set(CATEGORY_CODES)


def test_an_artifact_without_provenance_is_refused(tmp_path):
    """Load-time refusal, not a warning.

    A model whose training data is unrecorded cannot be described honestly to a
    responder, so it is not served at all.
    """
    from sklearn.dummy import DummyClassifier

    estimator = DummyClassifier(strategy="prior").fit([[0], [1]], ["A", "B"])
    path = tmp_path / "no-provenance.joblib"

    with pytest.raises(ArtifactError, match="data_provenance"):
        save_bundle(path, estimator=estimator, manifest={"name": "x", "version": "1"})


def test_a_missing_artifact_says_how_to_make_one(tmp_path):
    with pytest.raises(ArtifactError, match="export_baseline_model"):
        load_bundle(tmp_path / "absent.joblib")


def test_a_foreign_pickle_is_refused(tmp_path):
    import joblib

    path = tmp_path / "foreign.joblib"
    joblib.dump({"something": "else"}, path)
    with pytest.raises(ArtifactError, match="not a CampusShield bundle"):
        load_bundle(path)


# ---------------------------------------------------------------------------
# The risk scorer
# ---------------------------------------------------------------------------


def make_report(**overrides) -> Report:
    now = datetime.now(timezone.utc)
    defaults = {
        "public_ref": "CS-2026-TEST01",
        "report_kind": ReportKind.INCIDENT,
        "submission_mode": SubmissionMode.IDENTIFIED,
        "location_id": 1,
        "occurred_at": now - timedelta(hours=2),
        "occurred_hour": 14,
        "occurred_dow": 1,
        "submitted_at": now,
        "is_emergency": False,
        "is_ongoing": False,
    }
    defaults.update(overrides)
    return Report(**defaults)


def make_category(severity: int = 3) -> ReportCategory:
    return ReportCategory(
        code="TEST_CAT",
        label="Test category",
        kind=ReportKind.INCIDENT,
        routes_to_role=UserRole.ICC,
        base_severity=severity,
        emergency_eligible=True,
    )


def score(report=None, category=None, **kwargs):
    return score_report(
        report or make_report(),
        category if category is not None else make_category(),
        campus_timezone="Asia/Kolkata",
        now=datetime.now(timezone.utc),
        **kwargs,
    )


def test_an_emergency_scores_above_an_ordinary_report():
    ordinary = score()
    urgent = score(make_report(is_emergency=True))
    assert urgent.score > ordinary.score


def test_an_ongoing_emergency_scores_highest():
    result = score(make_report(is_emergency=True, is_ongoing=True))
    assert result.band in (RiskBand.HIGH, RiskBand.CRITICAL)


def test_a_more_severe_category_scores_higher():
    low = score(category=make_category(severity=1))
    high = score(category=make_category(severity=5))
    assert high.score > low.score


def test_a_missing_category_does_not_crash():
    result = score(category=None)
    assert 0 <= result.score <= 100


def test_the_score_is_capped_at_one_hundred():
    result = score(
        make_report(is_emergency=True, is_ongoing=True),
        make_category(severity=5),
        location_repeat_count=99,
    )
    assert result.score <= 100


def test_bands_are_ordered():
    assert band_for(90) is RiskBand.CRITICAL
    assert band_for(60) is RiskBand.HIGH
    assert band_for(30) is RiskBand.MODERATE
    assert band_for(5) is RiskBand.LOW


def test_the_scorer_cannot_read_a_reporter():
    """The signature is the guarantee.

    There is no principal, no attribution, no relationship to misuse. A future
    change that added one would have to change this signature, which is a
    conversation rather than an accident.
    """
    import inspect

    parameters = set(inspect.signature(score_report).parameters)
    assert parameters == {
        "report",
        "category",
        "campus_timezone",
        "location_repeat_count",
        "now",
    }


@pytest.mark.privacy
def test_factors_contain_no_credibility_term():
    result = score(make_report(is_emergency=True), location_repeat_count=3)
    assert not BARRED_FACTOR_KEYS & set(result.factors)
    assert not BARRED_FACTOR_KEYS & set(result.factors["weights"])
    assert not BARRED_FACTOR_KEYS & set(result.factors["contributions"])


@pytest.mark.privacy
def test_barred_keys_match_the_database_constraint(session):
    """The Python list and the CHECK must not drift apart.

    Read out of `pg_constraint` rather than duplicated here, so adding a term to
    one side without the other fails.
    """
    from sqlalchemy import text as sql_text

    definition = session.execute(
        sql_text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_risk_factors_no_credibility_terms'"
        )
    ).scalar_one()

    for term in BARRED_FACTOR_KEYS:
        assert term in definition, f"{term} is barred in Python but not in the database"


def test_factors_record_what_was_excluded_by_design():
    """The exclusions are stated in the record, not only in the code.

    Someone auditing a stored assessment should be able to see what the score
    deliberately did not consider without reading the source.
    """
    result = score()
    excluded = " ".join(result.factors["excluded_by_design"]).lower()
    assert "identity" in excluded
    assert "history" in excluded


def test_factors_carry_the_caveat_and_version():
    result = score()
    assert result.scorer_version == SCORER_VERSION
    caveat = result.factors["caveat"].lower()
    assert "not a prediction" in caveat
    assert "rule-based" in caveat or "rule_based" in result.factors["method"]


def test_every_contribution_is_traceable_to_a_weight():
    """A number that orders a queue has to be answerable for."""
    result = score(make_report(is_emergency=True, is_ongoing=True), location_repeat_count=2)
    for name in result.factors["contributions"]:
        assert name in result.factors["weights"], name
    assert round(sum(result.factors["contributions"].values()), 2) == result.score
