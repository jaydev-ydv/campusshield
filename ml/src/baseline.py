"""The baseline: TF-IDF + Logistic Regression.

Logistic Regression rather than LinearSVC, as the brief specifies, and for a
reason that matters downstream: it produces calibrated probabilities.
`ml.report_classification` has a `confidence` column and a `label_scores` JSONB
of the full distribution, and an SVM's decision-function margin is not a
probability. LinearSVC would need `CalibratedClassifierCV` wrapped around it to
fill those columns honestly.

`class_weight='balanced'` because the real category distribution will not be
uniform. Most reports will be a handful of common types; `VOYEURISM` and
`INTIMIDATION` will be rare and are the ones the institution most needs to see.
Without weighting, the optimiser is right to ignore them.

**The vectoriser is fitted on the training split only.** Fitting on all the text
first — a very easy mistake, since it is one line shorter — leaks test vocabulary
and document frequencies into training and inflates every number that follows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .labels import CATEGORY_CODES
from .metrics import compute_metrics
from .preprocess import preprocess_all
from .splitting import Split


@dataclass(slots=True)
class TrainedBaseline:
    pipeline: Pipeline
    train_seconds: float
    n_train: int
    n_features: int
    hyperparameters: dict[str, Any]


def build_pipeline(config: dict[str, Any], seed: int) -> Pipeline:
    vec = config["baseline"]["vectorizer"]
    clf = config["baseline"]["classifier"]

    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=tuple(vec["ngram_range"]),
                    min_df=vec["min_df"],
                    max_df=vec["max_df"],
                    sublinear_tf=vec["sublinear_tf"],
                    max_features=vec["max_features"],
                    # Preprocessing already ran; disabling these keeps the
                    # transformation in one place rather than half here and half
                    # in preprocess.py.
                    lowercase=False,
                    strip_accents=None,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    C=clf["C"],
                    max_iter=clf["max_iter"],
                    class_weight=clf["class_weight"],
                    random_state=seed,
                    n_jobs=None,
                ),
            ),
        ]
    )


def train(split: Split, config: dict[str, Any], seed: int) -> TrainedBaseline:
    pipeline = build_pipeline(config, seed)
    pre = config["preprocessing"]

    # Training split only. Nothing from val or test is visible here.
    x_train = preprocess_all([e.text for e in split.train], **pre)
    y_train = [e.label for e in split.train]

    started = time.perf_counter()
    pipeline.fit(x_train, y_train)
    elapsed = time.perf_counter() - started

    return TrainedBaseline(
        pipeline=pipeline,
        train_seconds=round(elapsed, 3),
        n_train=len(x_train),
        n_features=len(pipeline.named_steps["tfidf"].vocabulary_),
        hyperparameters={
            **config["baseline"]["vectorizer"],
            **config["baseline"]["classifier"],
            "seed": seed,
        },
    )


def evaluate(trained: TrainedBaseline, split: Split, config: dict[str, Any]) -> dict[str, Any]:
    """Score on the held-out test split — the same one the transformer sees."""
    pre = config["preprocessing"]
    x_test = preprocess_all([e.text for e in split.test], **pre)
    y_test = [e.label for e in split.test]

    started = time.perf_counter()
    predictions = list(trained.pipeline.predict(x_test))
    elapsed_ms = (time.perf_counter() - started) * 1000

    metrics = compute_metrics(y_test, predictions, labels=list(CATEGORY_CODES))
    metrics["mean_latency_ms"] = round(elapsed_ms / max(len(x_test), 1), 3)
    metrics["train_seconds"] = trained.train_seconds
    metrics["n_features"] = trained.n_features
    return metrics
