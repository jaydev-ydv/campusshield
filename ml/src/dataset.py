"""Dataset loading, with provenance attached at the source.

Every example carries where it came from, so a results file can never claim to
describe real-world performance when it was produced from generated text.

The SafeCity loader is a documented stub that **refuses to run**. The dataset has
no licence and requires prior permission that has not been obtained (DATASET.md
§2.1), and a loader that quietly works would make ignoring that a one-line
mistake.
"""

from __future__ import annotations

import pathlib
from typing import Any

from . import synthetic
from .config import ConfigError
from .synthetic import Example


def load_dataset(config: dict[str, Any], seed: int) -> tuple[list[Example], dict[str, Any]]:
    """Return examples plus a provenance record for the results file."""
    source = config["dataset"]["source"]

    if source == "synthetic":
        cfg = config["dataset"]["synthetic"]
        examples = synthetic.generate(
            examples_per_category=cfg["examples_per_category"],
            seed=seed,
            cross_category_noise=cfg["cross_category_noise"],
        )
        return examples, {
            "source": "synthetic",
            "provenance": "synthetic",
            "is_real_world_data": False,
            "generator": "ml/src/synthetic.py",
            "seed": seed,
            "examples_per_category": cfg["examples_per_category"],
            "cross_category_noise": cfg["cross_category_noise"],
            "caveat": (
                "SYNTHETIC DATA. Not collected from students, not annotated from real "
                "incidents. Metrics computed on it validate that the pipeline runs; they "
                "are not evidence of real-world performance and the baseline-vs-transformer "
                "gap is not informative. See DATASET.md section 4."
            ),
        }

    if source == "safecity":
        return _load_safecity(config)

    if source == "manual":
        return _load_manual(config)

    raise ConfigError(f"unknown dataset.source: {source!r}")


def _load_safecity(config: dict[str, Any]) -> tuple[list[Example], dict[str, Any]]:
    """SafeCity — NOT USABLE without written permission.

    Deliberately fails. The repository carries no LICENSE and its README requires
    contacting the moderators for permission before use. Until that permission
    exists and is recorded in DATASET.md, this must not load anything.

    When permission is obtained: set `permission_obtained: true`, record the
    permission in DATASET.md, and implement the reader below. Note that even
    then the label mapping covers only HARASS_PHYSICAL and HARASS_VERBAL —
    2 of 16 categories. See ml/src/labels.py:SAFECITY_MAPPING.
    """
    cfg = config["dataset"]["safecity"]
    if not cfg.get("permission_obtained", False):
        raise ConfigError(
            "SafeCity requires prior permission from its moderators "
            "(http://maps.safecity.in/contact) and the repository carries no licence. "
            "Set dataset.safecity.permission_obtained: true only after that permission "
            "has been granted AND recorded in ml/DATASET.md. See DATASET.md section 2.1."
        )
    path = pathlib.Path(cfg["path"])
    raise NotImplementedError(
        f"Permission is marked as obtained but the reader is not implemented. "
        f"Implement it against the files in {path}, mapping labels via "
        f"ml/src/labels.py:SAFECITY_MAPPING, and leave 'Ogling/Staring' unmapped."
    )


def _load_manual(config: dict[str, Any]) -> tuple[list[Example], dict[str, Any]]:
    """Human-authored, manually annotated examples.

    Intended to read from ml.annotation where source = 'manual_seed'. Not yet
    implemented because no annotation exercise has been run.
    """
    raise NotImplementedError(
        "No manual annotation set exists yet. See DATASET.md section 5(b) for the "
        "annotation protocol: 300-500 narratives, three annotators, Cohen's kappa reported."
    )


def label_distribution(examples: list[Example]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for example in examples:
        counts[example.label] = counts.get(example.label, 0) + 1
    return dict(sorted(counts.items()))
