"""The evaluation pipeline.

    python -m ml.src.evaluate

One command, one config file, one seed. Produces a machine-readable
`results/evaluation.json` and a human-readable `results/EVALUATION.md`.

Order matters and is not negotiable:

    load -> split -> LEAKAGE CHECK -> train -> evaluate -> write

The leakage check sits before training and **aborts** on failure. A pipeline that
warns and trains anyway emits a results file indistinguishable from a clean one,
and the number inside it would be measuring memorisation.

Both model arms are scored on the *same* `Split` object, so "identical held-out
test set" is true by construction rather than by discipline.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from . import baseline as baseline_mod
from . import hotspots, similarity, transformer
from .config import ML_ROOT, load_config
from .dataset import label_distribution, load_dataset
from .labels import CATEGORY_CODES, safecity_coverage
from .leakage import assert_no_leakage
from .metrics import confusion_matrix_markdown
from .splitting import stratified_group_split


def run(config_path: str | None = None, *, quick: bool = False) -> dict[str, Any]:
    config = load_config(config_path)
    seed = config["seed"]

    if quick:
        # Used by the test suite: a tiny transformer so no 250MB download and no
        # multi-minute fine-tune on every run.
        config["transformer"]["pretrained_model"] = config["transformer"]["test_pretrained_model"]
        config["transformer"]["epochs"] = 1
        config["dataset"]["synthetic"]["examples_per_category"] = 12

    print("CampusShield ML evaluation")
    print("=" * 64)

    # 1. Load ------------------------------------------------------------
    examples, provenance = load_dataset(config, seed)
    print(f"\nDataset: {provenance['source']} — {len(examples)} examples")
    if not provenance.get("is_real_world_data", False):
        print("  ** SYNTHETIC DATA — not evidence of real-world performance **")

    # 2. Split -----------------------------------------------------------
    split_cfg = config["split"]
    split = stratified_group_split(
        examples,
        test_size=split_cfg["test_size"],
        val_size=split_cfg["val_size"],
        seed=seed,
        deduplicate=split_cfg["deduplicate"],
        near_duplicate_threshold=split_cfg["near_duplicate_threshold"],
    )
    print(f"Split: {split.sizes}")

    # 3. Leakage — before any training ------------------------------------
    leakage_report = assert_no_leakage(
        split, near_duplicate_threshold=split_cfg["near_duplicate_threshold"]
    )
    print(f"Leakage checks: {len(leakage_report.findings)} passed")

    # 4. Baseline ---------------------------------------------------------
    print(f"\nTraining baseline ({config['baseline']['name']})…")
    trained = baseline_mod.train(split, config, seed)
    baseline_metrics = baseline_mod.evaluate(trained, split, config)
    baseline_metrics["status"] = "completed"
    baseline_metrics["hyperparameters"] = trained.hyperparameters
    print(
        f"  accuracy={baseline_metrics['accuracy']:.4f} "
        f"macro_f1={baseline_metrics['macro_f1']:.4f} "
        f"weighted_f1={baseline_metrics['weighted_f1']:.4f} "
        f"({trained.train_seconds}s)"
    )

    # 5. Transformer — same split ----------------------------------------
    print(f"\nTraining transformer ({config['transformer']['pretrained_model']})…")
    transformer_metrics = transformer.train_and_evaluate(split, config, seed)
    if transformer_metrics["status"] == "completed":
        print(
            f"  accuracy={transformer_metrics['accuracy']:.4f} "
            f"macro_f1={transformer_metrics['macro_f1']:.4f} "
            f"weighted_f1={transformer_metrics['weighted_f1']:.4f} "
            f"({transformer_metrics['train_seconds']}s)"
        )
    else:
        print(f"  NOT RUN: {transformer_metrics['reason']}")

    # 6. Assemble ---------------------------------------------------------
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "data_provenance": provenance,
        "label_space": {
            "categories": list(CATEGORY_CODES),
            "count": len(CATEGORY_CODES),
            "distribution": label_distribution(examples),
        },
        "split": {
            **split.sizes,
            "test_size": split_cfg["test_size"],
            "val_size": split_cfg["val_size"],
            "stratified": split_cfg["stratify"],
            "deduplicated": split_cfg["deduplicate"],
            "note": (
                "Duplicate and near-duplicate texts are grouped and assigned to a single "
                "split, so realised proportions differ slightly from the requested ones."
            ),
        },
        "leakage_checks": leakage_report.to_dict(),
        "primary_metric": config["evaluation"]["primary_metric"],
        "models": {
            "baseline": baseline_metrics,
            "transformer": transformer_metrics,
        },
        "comparison": _build_comparison(baseline_metrics, transformer_metrics, config),
        "related_report_detection": similarity.evaluation_strategy(),
        "hotspot_detection": hotspots.evaluation_strategy(),
        "external_dataset_assessment": {
            "safecity": {
                "usable": False,
                "reason": "No licence; prior permission required and not obtained.",
                "label_coverage_if_permitted": safecity_coverage(),
            }
        },
    }

    _write_results(results, config)
    return results


def _build_comparison(
    baseline_metrics: dict[str, Any],
    transformer_metrics: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """State which model won, or that the comparison could not be made.

    Never asserts the transformer is better unless the numbers say so, and always
    carries the caveat that a synthetic-data comparison is not evidence about
    real reports.
    """
    metric = config["evaluation"]["primary_metric"]

    if transformer_metrics.get("status") != "completed":
        return {
            "status": "incomplete",
            "primary_metric": metric,
            "detail": (
                "The transformer arm did not run, so no comparison exists. "
                f"Reason: {transformer_metrics.get('reason', 'unknown')}"
            ),
            "winner": None,
        }

    baseline_score = baseline_metrics[metric]
    transformer_score = transformer_metrics[metric]
    difference = round(transformer_score - baseline_score, 4)

    if difference > 0:
        winner, summary = "transformer", f"transformer higher by {difference:+.4f} {metric}"
    elif difference < 0:
        winner, summary = "baseline", f"baseline higher by {-difference:.4f} {metric}"
    else:
        winner, summary = "tie", f"identical {metric}"

    synthetic = not config["dataset"].get("provenance") == "public_dataset"

    # A near-perfect score is almost never a good model; it is a task that is too
    # easy. Reporting 1.0000 as an achievement would be the single most
    # misleading thing this pipeline could do, so it is flagged here rather than
    # left for a reader to notice.
    warnings: list[str] = []
    for name, score in (("baseline", baseline_score), ("transformer", transformer_score)):
        if score >= 0.99:
            warnings.append(
                f"The {name} scored {score:.4f} on {metric}. A near-perfect score "
                "indicates the evaluation task is too easy, not that the model is "
                "excellent. On synthetic text this is expected: the generator has a "
                "bounded vocabulary and consistent grammar, so a model with enough "
                "capacity can separate the classes almost perfectly. Real narratives "
                "have typos, ambiguity, and reports that fit two categories or none. "
                "This number must not be presented as model accuracy."
            )

    return {
        "status": "complete",
        "primary_metric": metric,
        "baseline": baseline_score,
        "transformer": transformer_score,
        "difference": difference,
        "winner": winner,
        "summary": summary,
        "warnings": warnings,
        "interpretable_as_model_quality": not warnings and not synthetic,
        "caveat": (
            "Measured on SYNTHETIC data. This does not establish which model performs "
            "better on real campus reports. Templated text rewards the surface lexical "
            "cues TF-IDF is built for, so the gap here is not transferable."
            if synthetic
            else None
        ),
    }


def _write_results(results: dict[str, Any], config: dict[str, Any]) -> None:
    results_dir = ML_ROOT / config["evaluation"]["results_dir"]
    results_dir.mkdir(parents=True, exist_ok=True)

    json_path = results_dir / "evaluation.json"
    json_path.write_text(json.dumps(results, indent=2))

    md_path = results_dir / "EVALUATION.md"
    md_path.write_text(render_markdown(results))

    print(f"\nWrote {json_path.relative_to(ML_ROOT.parent)}")
    print(f"Wrote {md_path.relative_to(ML_ROOT.parent)}")


def render_markdown(results: dict[str, Any]) -> str:
    provenance = results["data_provenance"]
    baseline_metrics = results["models"]["baseline"]
    transformer_metrics = results["models"]["transformer"]
    comparison = results["comparison"]
    is_real = provenance.get("is_real_world_data", False)

    lines: list[str] = [
        "# CampusShield — Classification Evaluation",
        "",
        f"Generated {results['generated_at']} · seed `{results['seed']}`",
        "",
    ]

    if not is_real:
        lines += [
            "> ## ⚠ These results come from SYNTHETIC data",
            ">",
            "> The text was generated by `ml/src/synthetic.py`. It was **not** collected",
            "> from students and **not** annotated from real incidents.",
            ">",
            "> **The numbers below demonstrate that the pipeline runs correctly and",
            "> reproducibly. They are not evidence of how either model would perform on",
            "> real campus reports, and the difference between the two models is not",
            "> transferable** — templated text rewards exactly the surface lexical cues",
            "> TF-IDF is built for.",
            ">",
            "> No external dataset could be legitimately used. See",
            "> [DATASET.md](../DATASET.md) for the full investigation.",
            "",
        ]

    lines += [
        "## Headline",
        "",
        f"Primary metric: **{results['primary_metric']}** — chosen because the real "
        "category distribution is imbalanced, and accuracy would let a model score well "
        "by predicting common categories and ignoring the rare ones.",
        "",
        "| Model | Accuracy | Macro F1 | Weighted F1 | Train (s) | Inference (ms/sample) |",
        "|---|---|---|---|---|---|",
    ]

    lines.append(
        f"| TF-IDF + Logistic Regression | {baseline_metrics['accuracy']:.4f} | "
        f"{baseline_metrics['macro_f1']:.4f} | {baseline_metrics['weighted_f1']:.4f} | "
        f"{baseline_metrics.get('train_seconds', '—')} | "
        f"{baseline_metrics.get('mean_latency_ms', '—')} |"
    )

    if transformer_metrics.get("status") == "completed":
        lines.append(
            f"| {transformer_metrics['model']} | {transformer_metrics['accuracy']:.4f} | "
            f"{transformer_metrics['macro_f1']:.4f} | {transformer_metrics['weighted_f1']:.4f} | "
            f"{transformer_metrics.get('train_seconds', '—')} | "
            f"{transformer_metrics.get('mean_latency_ms', '—')} |"
        )
    else:
        lines.append(
            f"| {transformer_metrics.get('model', 'transformer')} | _not run_ | _not run_ | "
            "_not run_ | — | — |"
        )
        lines += ["", f"**Transformer not run:** {transformer_metrics.get('reason')}"]

    lines += ["", "### Comparison", ""]
    if comparison["status"] == "complete":
        lines.append(f"On **{comparison['primary_metric']}**, the {comparison['summary']}.")
        if comparison.get("caveat"):
            lines += ["", f"**Caveat.** {comparison['caveat']}"]
        for warning in comparison.get("warnings", []):
            lines += ["", f"> **Read this before quoting the number above.** {warning}"]
        if not comparison.get("interpretable_as_model_quality", False):
            lines += [
                "",
                "**This comparison must not be reported as evidence that one model is "
                "better than the other.** It shows the pipeline runs and that both arms "
                "were scored on the same held-out split. Nothing more.",
            ]
    else:
        lines.append(comparison["detail"])

    # Split and leakage
    split = results["split"]
    lines += [
        "",
        "## Method",
        "",
        f"- **Split:** {split['train']} train / {split['val']} validation / "
        f"{split['test']} test, stratified by category.",
        f"- {split['note']}",
        "- **Both models were scored on the identical held-out test split.**",
        "",
        "### Leakage checks",
        "",
        "Run **before** training; the pipeline aborts on any failure rather than warning.",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for check in results["leakage_checks"]["checks"]:
        mark = "pass" if check["passed"] else "**FAIL**"
        lines.append(f"| `{check['check']}` | {mark} | {check['detail']} |")

    # Per-class
    lines += ["", "## Per-class results", "", "### Baseline", "", "| Category | Precision | Recall | F1 | Support |", "|---|---|---|---|---|"]
    for label, scores in baseline_metrics["per_class"].items():
        lines.append(
            f"| `{label}` | {scores['precision']:.4f} | {scores['recall']:.4f} | "
            f"{scores['f1']:.4f} | {scores['support']} |"
        )
    if baseline_metrics.get("never_predicted"):
        lines += [
            "",
            f"**Never predicted by the baseline:** "
            f"{', '.join('`' + c + '`' for c in baseline_metrics['never_predicted'])}. "
            "These score zero recall for a reason unrelated to the text.",
        ]

    if transformer_metrics.get("status") == "completed":
        lines += ["", "### Transformer", "", "| Category | Precision | Recall | F1 | Support |", "|---|---|---|---|---|"]
        for label, scores in transformer_metrics["per_class"].items():
            lines.append(
                f"| `{label}` | {scores['precision']:.4f} | {scores['recall']:.4f} | "
                f"{scores['f1']:.4f} | {scores['support']} |"
            )
        if transformer_metrics.get("never_predicted"):
            lines += [
                "",
                f"**Never predicted by the transformer:** "
                f"{', '.join('`' + c + '`' for c in transformer_metrics['never_predicted'])}.",
            ]

    # Confusion matrices
    lines += ["", "## Confusion matrices", "", "### Baseline", "", confusion_matrix_markdown(baseline_metrics["confusion_matrix"])]
    if transformer_metrics.get("status") == "completed":
        lines += ["", "### Transformer", "", confusion_matrix_markdown(transformer_metrics["confusion_matrix"])]

    # Configuration
    lines += ["", "## Configuration", "", "### Baseline", "", "```json", json.dumps(baseline_metrics.get("hyperparameters", {}), indent=2), "```"]
    if transformer_metrics.get("status") == "completed":
        lines += [
            "",
            "### Transformer",
            "",
            "```json",
            json.dumps(transformer_metrics.get("hyperparameters", {}), indent=2),
            "```",
            "",
            "**Hardware:** " + json.dumps(transformer_metrics.get("hardware", {})),
        ]

    # Not evaluated
    related = results["related_report_detection"]
    hotspot = results["hotspot_detection"]
    lines += [
        "",
        "## What is deliberately not measured here",
        "",
        f"- **Related-report detection:** {related['status']}. {related['reason']}",
        f"- **Hotspot detection:** {hotspot['status']}. {hotspot['reason']}",
        "",
        "Both ship as working, tested implementations with defined evaluation "
        "strategies. Neither carries an accuracy claim, because neither has a "
        "labelled set to be accurate against.",
        "",
        "## External dataset assessment",
        "",
    ]
    coverage = results["external_dataset_assessment"]["safecity"]["label_coverage_if_permitted"]
    lines += [
        "**SafeCity** — not usable. No licence, and prior permission is required and has "
        "not been obtained.",
        "",
        f"Even with permission it would cover **{coverage['covered_count']} of "
        f"{coverage['total_categories']} categories "
        f"({coverage['coverage_fraction'] * 100:.1f}%)**: "
        f"{', '.join('`' + c + '`' for c in coverage['covered_categories'])}. "
        f"The other {len(coverage['uncovered_categories'])} would have no training data.",
        "",
        "See [DATASET.md](../DATASET.md) for the full investigation.",
        "",
    ]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the CampusShield ML evaluation pipeline.")
    parser.add_argument("--config", default=None, help="path to a YAML config")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="tiny model and corpus, for a fast smoke run",
    )
    args = parser.parse_args()

    try:
        run(args.config, quick=args.quick)
    except Exception as exc:
        print(f"\nEvaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
