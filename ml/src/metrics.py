"""Metric computation.

One implementation, used by both model arms, so the comparison cannot drift
apart. The brief requires accuracy, macro F1, weighted F1, per-class precision
and recall, and a confusion matrix; all of them come from here.

`labels=` is passed explicitly on every call. Without it scikit-learn infers the
label set from whatever appears in y_true/y_pred, so a class the model never
predicts silently vanishes from the macro average — inflating it, and hiding
exactly the failure worth knowing about.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)


def compute_metrics(
    y_true: list[str],
    y_pred: list[str],
    *,
    labels: list[str],
) -> dict[str, Any]:
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must be the same length")
    if not y_true:
        raise ValueError("cannot compute metrics on an empty set")

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average=None, zero_division=0
    )

    per_class = {
        label: {
            "precision": round(float(precision[i]), 4),
            "recall": round(float(recall[i]), 4),
            "f1": round(float(f1[i]), 4),
            "support": int(support[i]),
        }
        for i, label in enumerate(labels)
    }

    matrix = confusion_matrix(y_true, y_pred, labels=labels)

    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)), 4),
        "weighted_f1": round(float(f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0)), 4),
        "precision_macro": round(float(np.mean(precision)), 4),
        "recall_macro": round(float(np.mean(recall)), 4),
        "per_class": per_class,
        "confusion_matrix": {
            "labels": labels,
            "matrix": matrix.tolist(),
        },
        "n_samples": len(y_true),
        # Classes the model never predicted at all. Invisible in the headline
        # numbers and often the most interesting thing in the run.
        "never_predicted": sorted(set(labels) - set(y_pred)),
    }


def classification_text_report(y_true: list[str], y_pred: list[str], *, labels: list[str]) -> str:
    return classification_report(y_true, y_pred, labels=labels, zero_division=0, digits=4)


def confusion_matrix_markdown(matrix: dict[str, Any], *, max_labels: int = 20) -> str:
    """Render a confusion matrix as markdown, for the human-readable report."""
    labels = matrix["labels"]
    rows = matrix["matrix"]
    if len(labels) > max_labels:
        return f"_Confusion matrix omitted: {len(labels)} labels is too wide to render._"

    short = [label[:10] for label in labels]
    out = ["| actual \\ predicted | " + " | ".join(short) + " |"]
    out.append("|" + "---|" * (len(labels) + 1))
    for i, label in enumerate(labels):
        cells = [f"**{v}**" if i == j else (str(v) if v else "·") for j, v in enumerate(rows[i])]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out)
