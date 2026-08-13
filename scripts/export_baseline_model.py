#!/usr/bin/env python3
"""Train the serving classifier and export it as an artifact.

    python scripts/export_baseline_model.py [--register]

Trains on the synthetic corpus described by `ml/configs/default.yaml`, evaluates
on a held-out split, and writes `ml/artifacts/<name>-<version>.joblib`. With
`--register` it also inserts a row into `ml.model_version` and makes it the
active model.

## Why the baseline and not DistilBERT

`ml/results/evaluation.json` records the transformer at macro-F1 1.0000 and the
baseline at 0.8667 on synthetic text — and the pipeline's own warning says a
near-perfect score means the task is too easy, not that the model is excellent.
That gap is **not evidence** that the transformer would be better on real
campus reports, so it is not a reason to serve it.

Against that non-reason sit real costs: ~14 ms versus ~0.013 ms per inference,
torch and transformers in the Flask process, and a model whose probabilities
cannot be inspected by a human. When the evidence for a change is
uninterpretable, the cheaper and more transparent option wins.

`ml/src/transformer.py` remains, and this script would export it if a real
evaluation ever justified it.

## Determinism

Same seed, same config, same artifact. The manifest records both.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.ml.artifact import save_bundle  # noqa: E402
from app.ml.text import TextNormalizer  # noqa: E402
from ml.src.dataset import load_dataset  # noqa: E402
from ml.src.splitting import stratified_group_split  # noqa: E402
from ml.src.baseline import build_pipeline  # noqa: E402
from ml.src.config import load_config  # noqa: E402
from ml.src.metrics import compute_metrics  # noqa: E402

ARTIFACT_DIR = PROJECT_ROOT / "ml" / "artifacts"
VERSION = "1.0.0"


def build_serving_pipeline(config: dict, seed: int):
    """The research pipeline with normalisation moved inside it.

    `ml/src/baseline.py` preprocesses before fitting, because the research
    pipeline evaluates many configurations over an already-normalised corpus.
    A served model has to take a raw narrative, so the same normalisation becomes
    the first step of the estimator — one place instead of two, and no way for
    the serving path to disagree with the training path.
    """
    pipeline = build_pipeline(config, seed)
    pipeline.steps.insert(0, ("normalise", TextNormalizer(**config["preprocessing"])))
    return pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default=str(PROJECT_ROOT / "ml" / "configs" / "default.yaml")
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="insert a row into ml.model_version and make it the active model",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    config = load_config(args.config)
    seed = config["seed"]

    print(f"config     {args.config}")
    print(f"seed       {seed}")

    corpus, provenance = load_dataset(config, seed)
    if provenance.get("is_real_world_data"):
        print(f"data       REAL-WORLD ({len(corpus)} examples)")
    else:
        print(f"data       SYNTHETIC ({len(corpus)} examples)")

    split_cfg = config["split"]
    split = stratified_group_split(
        corpus,
        test_size=split_cfg["test_size"],
        val_size=split_cfg["val_size"],
        seed=seed,
        deduplicate=split_cfg.get("deduplicate", True),
    )

    # Trained on the training split only, then scored on the held-out test split.
    # The numbers in the manifest are from data the model never saw.
    pipeline = build_serving_pipeline(config, seed)
    pipeline.fit([e.text for e in split.train], [e.label for e in split.train])

    predicted = pipeline.predict([e.text for e in split.test])
    metrics = compute_metrics(
        [e.label for e in split.test], list(predicted), labels=list(pipeline.classes_)
    )
    headline = {
        "macro_f1": metrics["macro_f1"],
        "accuracy": metrics["accuracy"],
        "n_test": len(split.test),
        "evaluated_on": "held-out test split of the synthetic corpus",
    }
    print(f"macro F1   {metrics['macro_f1']:.4f}  (accuracy {metrics['accuracy']:.4f})")

    name = config["baseline"]["name"]
    manifest = {
        "name": name,
        "version": VERSION,
        "family": "baseline",
        "task": "classification",
        "classes": list(pipeline.classes_),
        "seed": seed,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_rows": len(split.train),
        "hyperparameters": {
            **config["baseline"]["vectorizer"],
            **config["baseline"]["classifier"],
            "seed": seed,
        },
        "headline_metrics": headline,
        "data_provenance": provenance,
    }

    artifact_path = ARTIFACT_DIR / f"{name}-{VERSION}.joblib"
    save_bundle(artifact_path, estimator=pipeline, manifest=manifest)
    print(f"artifact   {artifact_path.relative_to(PROJECT_ROOT)} "
          f"({artifact_path.stat().st_size // 1024} KB)")

    if not args.register:
        print("\nNot registered. Re-run with --register to activate it.")
        return 0

    if not args.database_url:
        print("\nDATABASE_URL is not set; cannot register.", file=sys.stderr)
        return 1

    from sqlalchemy import create_engine, text

    engine = create_engine(args.database_url, future=True)
    relative_uri = str(artifact_path.relative_to(PROJECT_ROOT))
    with engine.begin() as connection:
        # One active classifier at a time. Deactivating first means there is
        # never a moment with two, which the serving code would have to
        # arbitrate between.
        connection.execute(
            text("UPDATE ml.model_version SET is_active = FALSE WHERE task = 'classification'")
        )
        model_id = connection.execute(
            text(
                """
                INSERT INTO ml.model_version
                    (name, task, family, version, artifact_uri, trained_at,
                     training_rows, hyperparameters, headline_metrics, is_active)
                VALUES
                    (:name, 'classification', 'baseline', :version, :uri, :trained_at,
                     :rows, CAST(:hyper AS jsonb), CAST(:metrics AS jsonb), TRUE)
                RETURNING model_id
                """
            ),
            {
                "name": name,
                "version": VERSION,
                "uri": relative_uri,
                "trained_at": manifest["trained_at"],
                "rows": manifest["training_rows"],
                "hyper": json.dumps(manifest["hyperparameters"]),
                # The provenance travels into the database too, so a query
                # against ml.model_version alone shows what a model was trained
                # on without anyone opening the artifact.
                "metrics": json.dumps({**headline, "data_provenance": provenance}),
            },
        ).scalar_one()

    print(f"registered {model_id} (active)")
    if not provenance.get("is_real_world_data"):
        print(
            "\nNOTE: this model is trained on SYNTHETIC data. Its suggestions are\n"
            "      decision support only and carry no real-world accuracy claim."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
