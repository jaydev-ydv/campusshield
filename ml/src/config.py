"""Configuration loading.

Every parameter comes from YAML so a run is reproducible from the config file
plus a git commit, rather than from whatever was hard-coded at the time.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ML_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ML_ROOT / "configs" / "default.yaml"

VALID_PROVENANCE = {"synthetic", "public_dataset", "manual_annotation", "production_override"}


class ConfigError(RuntimeError):
    """The configuration cannot produce a defensible run."""


def load_config(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    config_path = pathlib.Path(path) if path else DEFAULT_CONFIG
    if not config_path.is_file():
        raise ConfigError(f"config not found: {config_path}")

    with config_path.open() as handle:
        config = yaml.safe_load(handle)

    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    """Reject a configuration that would produce an unattributable result.

    Provenance is checked here rather than at write time because a run that
    cannot say where its data came from should not consume twenty minutes of
    training first.
    """
    provenance = config.get("dataset", {}).get("provenance")
    if provenance not in VALID_PROVENANCE:
        raise ConfigError(
            f"dataset.provenance must be one of {sorted(VALID_PROVENANCE)}, got {provenance!r}. "
            "Every artifact records where its data came from; see DATASET.md §6."
        )

    source = config.get("dataset", {}).get("source")
    if source == "synthetic" and provenance != "synthetic":
        raise ConfigError(
            "dataset.source is 'synthetic' but provenance claims otherwise. "
            "Synthetic text must never be recorded as real-world data."
        )

    if config.get("evaluation", {}).get("primary_metric") not in {
        "macro_f1",
        "weighted_f1",
        "accuracy",
    }:
        raise ConfigError("evaluation.primary_metric must be macro_f1, weighted_f1 or accuracy")


def resolve_path(config_relative: str) -> pathlib.Path:
    return ML_ROOT / config_relative
