"""Tests for the ML evaluation pipeline.

**No test downloads a transformer.** The transformer arm is exercised through
its availability check and its not-run path, both of which are mocked. Actually
fine-tuning a model is what `python -m ml.src.evaluate` is for, not a test suite.

The leakage tests are the ones that matter most. If they pass while leakage is
present, every metric the project reports is worthless — so several of them
construct deliberately broken splits and assert that the pipeline refuses to
proceed.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from ml.src import baseline as baseline_mod
from ml.src import hotspots, similarity, synthetic
from ml.src.config import ConfigError, load_config, validate_config
from ml.src.dataset import label_distribution, load_dataset
from ml.src.labels import (
    CATEGORIES,
    CATEGORY_CODES,
    SAFECITY_MAPPING,
    from_index,
    safecity_coverage,
    to_index,
)
from ml.src.leakage import LeakageError, assert_no_leakage, run_all_checks
from ml.src.metrics import compute_metrics
from ml.src.preprocess import preprocess
from ml.src.splitting import Split, group_duplicates, stratified_group_split, text_hash
from ml.src.synthetic import Example
from ml.src.transformer import check_availability

SEED = 20260811
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def corpus():
    return synthetic.generate(examples_per_category=20, seed=SEED, cross_category_noise=0.25)


@pytest.fixture(scope="module")
def split(corpus):
    return stratified_group_split(corpus, test_size=0.2, val_size=0.1, seed=SEED)


# ---------------------------------------------------------------------------
# Label space
# ---------------------------------------------------------------------------


def test_label_space_matches_the_database_seed():
    """The model's label space must be the categories a student can actually pick.

    If these drift, the classifier predicts something the report form cannot
    offer.
    """
    sql = (PROJECT_ROOT / "sql" / "seed_report_categories.sql").read_text()
    for code in CATEGORY_CODES:
        assert f"'{code}'" in sql, f"{code} is not in the database seed"


def test_sixteen_categories_across_two_kinds():
    assert len(CATEGORIES) == 16
    assert len({c.code for c in CATEGORIES}) == 16
    assert sum(1 for c in CATEGORIES if c.kind.value == "incident") == 9
    assert sum(1 for c in CATEGORIES if c.kind.value == "concern") == 7


def test_label_index_round_trips():
    codes = ["HARASS_VERBAL", "LIGHTING_POOR", "STALKING"]
    assert from_index(to_index(codes)) == codes


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError, match="outside the controlled category set"):
        to_index(["NOT_A_CATEGORY"])


# ---------------------------------------------------------------------------
# Label mapping
# ---------------------------------------------------------------------------


def test_safecity_ogling_is_left_unmapped():
    """The honest outcome, not a forced one.

    Ogling is non-verbal so HARASS_VERBAL is wrong, and nothing is recorded so
    VOYEURISM is wrong. Inventing a mapping would train the model to be
    confidently incorrect.
    """
    ogling = next(m for m in SAFECITY_MAPPING if "Ogling" in m.external_label)
    assert ogling.campusshield_code is None
    assert ogling.quality == "unmapped"
    assert len(ogling.rationale) > 40


def test_safecity_coverage_is_computed_not_asserted():
    coverage = safecity_coverage()
    assert coverage["covered_count"] == 2
    assert coverage["total_categories"] == 16
    assert coverage["coverage_fraction"] == pytest.approx(0.125)
    assert set(coverage["covered_categories"]) == {"HARASS_PHYSICAL", "HARASS_VERBAL"}
    assert len(coverage["uncovered_categories"]) == 14


def test_every_mapped_code_is_a_real_category():
    for mapping in SAFECITY_MAPPING:
        if mapping.campusshield_code:
            assert mapping.campusshield_code in CATEGORY_CODES


# ---------------------------------------------------------------------------
# Dataset loading and provenance
# ---------------------------------------------------------------------------


def test_synthetic_loading_records_provenance(config):
    examples, provenance = load_dataset(config, SEED)
    assert provenance["provenance"] == "synthetic"
    assert provenance["is_real_world_data"] is False
    assert "SYNTHETIC DATA" in provenance["caveat"]
    assert len(examples) > 0


def test_synthetic_generation_is_deterministic():
    a = synthetic.generate(examples_per_category=10, seed=SEED, cross_category_noise=0.25)
    b = synthetic.generate(examples_per_category=10, seed=SEED, cross_category_noise=0.25)
    assert [e.text for e in a] == [e.text for e in b]


def test_a_different_seed_gives_different_text():
    a = synthetic.generate(examples_per_category=10, seed=1, cross_category_noise=0.25)
    b = synthetic.generate(examples_per_category=10, seed=2, cross_category_noise=0.25)
    assert [e.text for e in a] != [e.text for e in b]


def test_every_category_is_represented(corpus):
    assert set(label_distribution(corpus)) == set(CATEGORY_CODES)


def test_every_example_is_marked_synthetic(corpus):
    assert all(e.provenance == "synthetic" for e in corpus)


def test_categories_share_vocabulary():
    """Without lexical overlap the task is trivially separable and the
    comparison measures nothing."""
    examples = synthetic.generate(examples_per_category=30, seed=SEED, cross_category_noise=0.25)
    by_label: dict[str, set[str]] = {}
    for example in examples:
        by_label.setdefault(example.label, set()).update(example.text.lower().split())

    verbal = by_label["HARASS_VERBAL"]
    lighting = by_label["LIGHTING_POOR"]
    overlap = len(verbal & lighting) / len(verbal | lighting)
    assert overlap > 0.1, f"categories share only {overlap:.1%} of vocabulary"


def test_safecity_loader_refuses_without_permission(config):
    """The dataset has no licence and requires prior permission. A loader that
    quietly worked would make ignoring that a one-line mistake."""
    cfg = json.loads(json.dumps(config))
    cfg["dataset"]["source"] = "safecity"
    with pytest.raises(ConfigError, match="permission"):
        load_dataset(cfg, SEED)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_requires_valid_provenance(config):
    cfg = json.loads(json.dumps(config))
    cfg["dataset"]["provenance"] = "real_data_honest"
    with pytest.raises(ConfigError, match="provenance"):
        validate_config(cfg)


def test_config_refuses_to_call_synthetic_data_real(config):
    cfg = json.loads(json.dumps(config))
    cfg["dataset"]["source"] = "synthetic"
    cfg["dataset"]["provenance"] = "public_dataset"
    with pytest.raises(ConfigError, match="never be recorded as real-world"):
        validate_config(cfg)


def test_primary_metric_is_stated_and_valid(config):
    assert config["evaluation"]["primary_metric"] == "macro_f1"


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


def test_preprocessing_is_deterministic():
    text = "  A Report   with  https://example.com and  SPACES  "
    assert preprocess(text) == preprocess(text)


def test_preprocessing_strips_urls_and_normalises_space():
    assert "example.com" not in preprocess("see https://example.com/x now")
    assert preprocess("  a   b  ") == "a b"


def test_preprocessing_keeps_negation():
    """Stopword removal would delete 'not', and 'would not stop' means the
    opposite of 'would stop'."""
    assert "not" in preprocess("he would not stop following me")


def test_preprocessing_handles_none_and_empty():
    assert preprocess(None) == ""
    assert preprocess("") == ""


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_split_is_deterministic(corpus):
    a = stratified_group_split(corpus, test_size=0.2, val_size=0.1, seed=SEED)
    b = stratified_group_split(corpus, test_size=0.2, val_size=0.1, seed=SEED)
    assert [e.example_id for e in a.test] == [e.example_id for e in b.test]


def test_split_covers_every_example_exactly_once(corpus, split):
    ids = [e.example_id for e in split.all_examples()]
    assert len(ids) == len(corpus)
    assert len(set(ids)) == len(corpus)


def test_split_is_stratified(split):
    """Every category must appear in train and test, or per-class metrics for
    the missing ones are meaningless."""
    assert {e.label for e in split.train} == set(CATEGORY_CODES)
    assert {e.label for e in split.test} == set(CATEGORY_CODES)


def test_duplicate_texts_are_grouped():
    examples = [
        Example("Someone followed me to the car park.", "STALKING", example_id="a"),
        Example("someone followed me to the car park", "STALKING", example_id="b"),
        Example("The lights are broken near the gate.", "LIGHTING_POOR", example_id="c"),
    ]
    groups = group_duplicates(examples)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2], "case and punctuation differences must not create two groups"


def test_duplicates_never_cross_the_split_boundary():
    """The reason grouping exists. Twelve copies of one text, split — every copy
    must land on the same side."""
    duplicated = [
        Example("Identical narrative text for this test.", "STALKING", example_id=f"dup-{i}")
        for i in range(12)
    ]
    other = [
        Example(f"A different narrative number {i} about lighting.", "LIGHTING_POOR",
                example_id=f"other-{i}")
        for i in range(12)
    ]
    split = stratified_group_split(duplicated + other, test_size=0.3, val_size=0.1, seed=SEED)

    where = {
        "train": {e.example_id for e in split.train if e.example_id.startswith("dup")},
        "val": {e.example_id for e in split.val if e.example_id.startswith("dup")},
        "test": {e.example_id for e in split.test if e.example_id.startswith("dup")},
    }
    non_empty = [name for name, ids in where.items() if ids]
    assert len(non_empty) == 1, f"duplicate group was split across {non_empty}"


def test_near_duplicates_are_grouped():
    examples = [
        Example("A man followed me from the gate to the hostel late at night.", "STALKING", example_id="a"),
        Example("A man followed me from the gate to the hostel late at night", "STALKING", example_id="b"),
        Example("The lights near the parking area have been broken for weeks.", "LIGHTING_POOR", example_id="c"),
    ]
    groups = group_duplicates(examples, near_duplicate_threshold=0.9)
    assert sorted(len(g) for g in groups) == [1, 2]


def test_split_rejects_impossible_proportions(corpus):
    with pytest.raises(ValueError):
        stratified_group_split(corpus, test_size=0.9, val_size=0.2, seed=SEED)


# ---------------------------------------------------------------------------
# Leakage — the checks that decide whether any metric is trustworthy
# ---------------------------------------------------------------------------


def test_a_clean_split_passes_every_check(split):
    report = run_all_checks(split)
    assert report.passed, [f.detail for f in report.failures()]
    assert len(report.findings) == 5


def test_exact_overlap_is_detected():
    shared = Example("A narrative that appears in both splits.", "STALKING", example_id="x")
    leaked = Split(
        train=[shared, Example("Other training text here.", "STALKING", example_id="t1")],
        test=[Example("A narrative that appears in both splits.", "STALKING", example_id="x2")],
    )
    report = run_all_checks(leaked)
    assert not report.passed
    assert any(f.check == "exact_text_overlap" and not f.passed for f in report.findings)


def test_near_duplicate_overlap_is_detected():
    """The check the hash comparison cannot make."""
    leaked = Split(
        train=[Example("A man followed me from the north gate to the hostel at night.", "STALKING", example_id="t1")],
        test=[Example("A man followed me from the north gate to the hostel at night", "STALKING", example_id="e1")],
    )
    report = run_all_checks(leaked)
    assert any(f.check == "near_duplicate_overlap" and not f.passed for f in report.findings)


def test_example_id_overlap_is_detected():
    same_id = "shared-id"
    leaked = Split(
        train=[Example("Training narrative one.", "STALKING", example_id=same_id)],
        test=[Example("A completely different test narrative.", "STALKING", example_id=same_id)],
    )
    report = run_all_checks(leaked)
    assert any(f.check == "example_id_overlap" and not f.passed for f in report.findings)


def test_unseen_test_label_is_detected():
    """Not leakage, but the same class of problem: a class absent from training
    scores zero for reasons unrelated to the model."""
    split = Split(
        train=[Example(f"Training text {i} about stalking.", "STALKING", example_id=f"t{i}") for i in range(15)],
        test=[Example(f"Test text {i} about lighting.", "LIGHTING_POOR", example_id=f"e{i}") for i in range(12)],
    )
    report = run_all_checks(split)
    assert any(f.check == "label_coverage" and not f.passed for f in report.findings)


def test_degenerate_split_is_detected():
    split = Split(train=[Example("Only one training example.", "STALKING", example_id="t")], test=[])
    report = run_all_checks(split)
    assert any(f.check == "split_sizes" and not f.passed for f in report.findings)


def test_leakage_aborts_rather_than_warns():
    """A pipeline that warns and trains anyway emits a results file that looks
    exactly like a clean one."""
    leaked = Split(
        train=[Example("Shared narrative between splits.", "STALKING", example_id="a")]
        + [Example(f"Filler {i}.", "STALKING", example_id=f"f{i}") for i in range(15)],
        test=[Example("Shared narrative between splits.", "STALKING", example_id="b")]
        + [Example(f"Test filler {i}.", "STALKING", example_id=f"tf{i}") for i in range(11)],
    )
    with pytest.raises(LeakageError, match="Refusing to train"):
        assert_no_leakage(leaked)


def test_the_real_pipeline_split_has_no_leakage(config):
    """The split the project actually reports on."""
    examples, _ = load_dataset(config, SEED)
    split = stratified_group_split(
        examples,
        test_size=config["split"]["test_size"],
        val_size=config["split"]["val_size"],
        seed=SEED,
        deduplicate=True,
    )
    assert assert_no_leakage(split).passed


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_perfect_predictions_score_one():
    labels = ["HARASS_VERBAL", "STALKING"]
    m = compute_metrics(labels, labels, labels=labels)
    assert m["accuracy"] == 1.0
    assert m["macro_f1"] == 1.0


def test_metrics_include_everything_the_brief_requires():
    y_true = ["HARASS_VERBAL", "STALKING", "HARASS_VERBAL"]
    y_pred = ["HARASS_VERBAL", "HARASS_VERBAL", "HARASS_VERBAL"]
    m = compute_metrics(y_true, y_pred, labels=["HARASS_VERBAL", "STALKING"])

    for key in ("accuracy", "macro_f1", "weighted_f1", "per_class", "confusion_matrix"):
        assert key in m
    assert "precision" in m["per_class"]["HARASS_VERBAL"]
    assert "recall" in m["per_class"]["HARASS_VERBAL"]


def test_macro_f1_counts_classes_the_model_never_predicts():
    """The whole point of passing labels= explicitly. Without it a never-predicted
    class vanishes from the macro average and inflates it."""
    y_true = ["HARASS_VERBAL", "STALKING"]
    y_pred = ["HARASS_VERBAL", "HARASS_VERBAL"]
    m = compute_metrics(y_true, y_pred, labels=["HARASS_VERBAL", "STALKING", "VOYEURISM"])

    assert "VOYEURISM" in m["per_class"]
    assert m["per_class"]["VOYEURISM"]["f1"] == 0.0
    assert m["macro_f1"] < m["accuracy"]


def test_never_predicted_classes_are_surfaced():
    m = compute_metrics(
        ["HARASS_VERBAL", "STALKING"],
        ["HARASS_VERBAL", "HARASS_VERBAL"],
        labels=["HARASS_VERBAL", "STALKING"],
    )
    assert "STALKING" in m["never_predicted"]


def test_confusion_matrix_is_square_and_ordered():
    labels = ["A", "B", "C"]
    m = compute_metrics(["A", "B", "C"], ["A", "B", "C"], labels=labels)
    matrix = m["confusion_matrix"]
    assert matrix["labels"] == labels
    assert len(matrix["matrix"]) == 3
    assert all(len(row) == 3 for row in matrix["matrix"])


def test_metrics_reject_mismatched_input():
    with pytest.raises(ValueError):
        compute_metrics(["A"], ["A", "B"], labels=["A", "B"])
    with pytest.raises(ValueError):
        compute_metrics([], [], labels=["A"])


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def test_baseline_trains_and_scores(config, split):
    trained = baseline_mod.train(split, config, SEED)
    metrics = baseline_mod.evaluate(trained, split, config)

    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["macro_f1"] <= 1.0
    assert metrics["n_samples"] == len(split.test)


def test_baseline_is_reproducible(config, split):
    a = baseline_mod.evaluate(baseline_mod.train(split, config, SEED), split, config)
    b = baseline_mod.evaluate(baseline_mod.train(split, config, SEED), split, config)
    assert a["accuracy"] == b["accuracy"]
    assert a["macro_f1"] == b["macro_f1"]


def test_vectoriser_is_fitted_on_training_data_only(config, split):
    """A vocabulary built from all the text leaks test document frequencies into
    training and inflates every number after it."""
    trained = baseline_mod.train(split, config, SEED)
    vectorizer = trained.pipeline.named_steps["tfidf"]
    vocabulary = set(vectorizer.vocabulary_)

    # Tokenised by the vectoriser's own analyzer, so the comparison is not
    # defeated by punctuation the analyzer strips and a naive split() keeps.
    analyzer = vectorizer.build_analyzer()
    train_terms = {term for e in split.train for term in analyzer(preprocess(e.text))}

    unseen = vocabulary - train_terms
    assert not unseen, f"vocabulary contains terms absent from training: {sorted(unseen)[:5]}"


def test_baseline_uses_logistic_regression(config):
    """The brief specifies it, and calibrated probabilities are what
    ml.report_classification.confidence expects."""
    pipeline = baseline_mod.build_pipeline(config, SEED)
    assert type(pipeline.named_steps["clf"]).__name__ == "LogisticRegression"


# ---------------------------------------------------------------------------
# Transformer — configuration and the not-run path only
# ---------------------------------------------------------------------------


def test_transformer_configuration_is_complete(config):
    cfg = config["transformer"]
    for key in ("pretrained_model", "max_length", "epochs", "learning_rate", "batch_size"):
        assert key in cfg, f"{key} must be recorded for reproducibility"


def test_transformer_uses_a_lightweight_model(config):
    """An undergraduate prototype fine-tuning on CPU. Something larger would cost
    hours and prove nothing extra."""
    assert "distilbert" in config["transformer"]["pretrained_model"].lower()


def test_transformer_reports_not_run_rather_than_inventing_numbers(config, split):
    """The single most important test in this file. An absent number is a
    finding; a fabricated one is misconduct."""
    from ml.src.transformer import TransformerAvailability

    with patch(
        "ml.src.transformer.check_availability",
        return_value=TransformerAvailability(available=False, reason="torch not installed"),
    ):
        result = __import__("ml.src.transformer", fromlist=["x"]).train_and_evaluate(
            split, config, SEED
        )

    assert result["status"] == "not_run"
    assert "torch not installed" in result["reason"]
    for forbidden in ("accuracy", "macro_f1", "weighted_f1"):
        assert forbidden not in result, f"a not-run arm must not report {forbidden}"


def test_availability_check_does_not_raise():
    result = check_availability()
    assert isinstance(result.available, bool)
    assert result.reason


# ---------------------------------------------------------------------------
# Related-report detection
# ---------------------------------------------------------------------------


def _candidate(rid: str, text: str, **kwargs):
    return similarity.SimilarityCandidate(report_id=rid, text=text, **kwargs)


def test_similarity_finds_near_identical_text():
    corpus = [
        _candidate("1", "A man followed me from the gate to the hostel late at night."),
        _candidate("2", "A man followed me from the gate to the hostel late at night."),
        _candidate("3", "The lights near the parking area have been broken for weeks."),
    ]
    results = similarity.find_related(corpus[0], corpus, related_threshold=0.5)
    assert results
    assert results[0].report_id == "2"


def test_similarity_excludes_the_report_itself():
    corpus = [_candidate("1", "Some narrative text about following."), _candidate("2", "Some narrative text about following.")]
    assert all(r.report_id != "1" for r in similarity.find_related(corpus[0], corpus, related_threshold=0.1))


def test_unrelated_text_is_below_threshold():
    corpus = [
        _candidate("1", "A man followed me across the parking area at night."),
        _candidate("2", "The gate lock has been broken since last term."),
    ]
    assert similarity.find_related(corpus[0], corpus, related_threshold=0.72) == []


def test_top_k_is_respected():
    corpus = [_candidate(str(i), "A man followed me from the gate to the hostel.") for i in range(10)]
    assert len(similarity.find_related(corpus[0], corpus, related_threshold=0.1, top_k=3)) == 3


def test_results_are_ordered_by_similarity():
    corpus = [
        _candidate("q", "A man followed me from the gate to the hostel at night."),
        _candidate("a", "A man followed me from the gate to the hostel at night."),
        _candidate("b", "A man followed me near the gate."),
    ]
    results = similarity.find_related(corpus[0], corpus, related_threshold=0.1)
    assert [r.similarity for r in results] == sorted((r.similarity for r in results), reverse=True)


def test_same_place_and_time_is_a_duplicate():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    text = "A man followed me from the gate to the hostel late at night."
    corpus = [
        _candidate("1", text, location_id=5, occurred_at=now),
        _candidate("2", text, location_id=5, occurred_at=now + timedelta(hours=2)),
    ]
    results = similarity.find_related(corpus[0], corpus, related_threshold=0.5, duplicate_threshold=0.8)
    assert results[0].relationship == "duplicate"


def test_a_different_location_is_related_not_duplicate():
    """The same hazard at two gates is two real problems. Merging them hides one."""
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    text = "The lights along this path have not worked for two weeks."
    corpus = [
        _candidate("1", text, location_id=5, occurred_at=now),
        _candidate("2", text, location_id=9, occurred_at=now),
    ]
    results = similarity.find_related(corpus[0], corpus, related_threshold=0.5, duplicate_threshold=0.8)
    assert results[0].relationship == "related"
    assert "different location" in results[0].rationale


def test_a_long_gap_is_a_recurrence_not_a_duplicate():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    text = "The lights along this path have not worked for two weeks."
    corpus = [
        _candidate("1", text, location_id=5, occurred_at=now),
        _candidate("2", text, location_id=5, occurred_at=now + timedelta(days=90)),
    ]
    results = similarity.find_related(
        corpus[0], corpus, related_threshold=0.5, duplicate_threshold=0.8, duplicate_max_days_apart=14
    )
    assert results[0].relationship == "related"
    assert "recurrence" in results[0].rationale


def test_thresholds_must_be_ordered():
    corpus = [_candidate("1", "text one"), _candidate("2", "text two")]
    with pytest.raises(ValueError, match="duplicate_threshold must be"):
        similarity.find_related(corpus[0], corpus, related_threshold=0.9, duplicate_threshold=0.5)


def test_threshold_controls_what_is_returned():
    corpus = [
        _candidate("1", "A man followed me from the gate to the hostel at night."),
        _candidate("2", "A man followed me near the gate."),
    ]
    permissive = similarity.find_related(
        corpus[0], corpus, related_threshold=0.1, duplicate_threshold=0.88
    )
    # duplicate_threshold must stay >= related_threshold, so it is raised too.
    strict = similarity.find_related(
        corpus[0], corpus, related_threshold=0.99, duplicate_threshold=0.99
    )
    assert len(permissive) > len(strict)


def test_similarity_candidate_cannot_carry_identity():
    """Structural, not conventional. There is no field to reach for."""
    fields = similarity.SimilarityCandidate.__slots__
    for forbidden in ("user_id", "reporter_id", "reporter_relationship", "credibility", "email"):
        assert forbidden not in fields


def test_similarity_claims_no_accuracy():
    strategy = similarity.evaluation_strategy()
    assert strategy["status"] == "not_evaluated"
    assert "fabrication" in strategy["reason"].lower()


def test_empty_corpus_returns_nothing():
    assert similarity.find_related(_candidate("1", "text"), []) == []


# ---------------------------------------------------------------------------
# Hotspots
# ---------------------------------------------------------------------------


def _observation(rid: str, location: int, days_ago: int, category: str = "HARASS_VERBAL", hour: int = 21):
    return hotspots.HotspotObservation(
        report_id=rid,
        location_id=location,
        occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc) - timedelta(days=days_ago),
        category_code=category,
        occurred_hour=hour,
    )


def test_hotspot_needs_enough_reports():
    observations = [_observation(f"r{i}", 1, i) for i in range(2)]
    assert hotspots.detect_hotspots(observations, min_reports=3) == []


def test_hotspot_is_detected_at_the_threshold():
    observations = [_observation(f"r{i}", 1, i) for i in range(4)]
    found = hotspots.detect_hotspots(observations, min_reports=3)
    assert len(found) == 1
    assert found[0].location_id == 1
    assert found[0].report_count == 4


def test_reports_outside_the_window_are_excluded():
    observations = [_observation(f"r{i}", 1, i) for i in range(3)] + [
        _observation(f"old{i}", 1, 200 + i) for i in range(5)
    ]
    found = hotspots.detect_hotspots(observations, window_days=30, min_reports=3)
    assert found[0].report_count == 3


def test_hotspots_are_ordered_by_report_count():
    observations = [_observation(f"a{i}", 1, i) for i in range(3)] + [
        _observation(f"b{i}", 2, i) for i in range(6)
    ]
    found = hotspots.detect_hotspots(observations, min_reports=3)
    assert found[0].location_id == 2


def test_dominant_category_and_hour_band_are_reported():
    observations = [_observation(f"r{i}", 1, i, category="STALKING", hour=22) for i in range(4)]
    found = hotspots.detect_hotspots(observations, min_reports=3)
    assert found[0].dominant_category == "STALKING"
    assert found[0].dominant_hour_band == "night"


def test_publication_respects_the_k_threshold():
    """Mirrors analytics.v_public_safety_map. Below k an aggregate can identify
    both the incident and the reporter."""
    observations = [_observation(f"r{i}", 1, i) for i in range(2)]
    found = hotspots.detect_hotspots(observations, min_reports=1, min_reports_for_publication=3)
    assert found[0].publishable is False


def test_hotspot_observation_cannot_carry_identity():
    fields = hotspots.HotspotObservation.__slots__
    for forbidden in ("user_id", "reporter_id", "reporter_relationship", "credibility"):
        assert forbidden not in fields


def test_hotspots_claim_no_accuracy():
    strategy = hotspots.evaluation_strategy()
    assert strategy["status"] == "not_evaluated"


def test_no_observations_gives_no_hotspots():
    assert hotspots.detect_hotspots([]) == []


def test_hour_bands_cover_the_clock():
    assert {hotspots.hour_band(h) for h in range(24)} == {
        "early_morning", "morning", "afternoon", "evening", "night", "late_night"
    }
