"""Loading the trained model the application serves.

## The provenance rule

An artifact **cannot be loaded without provenance**. `load_bundle` raises if the
manifest is missing `data_provenance`, and every prediction carries that
provenance outward to the API and the interface.

This is not ceremony. Every model in this project is trained on synthetic text —
`ml/DATASET.md` records why no external corpus could be legitimately used — and a
suggestion from such a model must never reach a responder looking like a finding
from a system trained on real reports. Making provenance a load-time requirement
means a future artifact trained on real data has to say so explicitly, and one
trained on synthetic data cannot quietly stop saying so.

## Why a bundle rather than a bare pickle

The manifest travels with the estimator, so the row in `ml.model_version` and the
file on disk cannot disagree about which model this is, what it scored, or what
it was trained on.

## Loading is lazy and cached

The artifact is a few hundred kilobytes and loads in milliseconds, but loading it
per request would still be waste. It is read once per process on first use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = 1


class ArtifactError(RuntimeError):
    """The artifact is missing, unreadable, or not something we will serve."""


@dataclass(frozen=True, slots=True)
class ModelBundle:
    """A trained estimator plus everything needed to describe it honestly."""

    estimator: Any
    name: str
    version: str
    family: str
    task: str
    classes: tuple[str, ...]
    provenance: dict[str, Any]
    hyperparameters: dict[str, Any]
    headline_metrics: dict[str, Any]
    trained_at: str | None
    training_rows: int | None

    @property
    def is_real_world_trained(self) -> bool:
        """Whether the training data came from the world rather than a generator.

        False for every artifact this project can currently produce. The
        interface reads this to decide how a suggestion is framed, so it is a
        property rather than a comment.
        """
        return bool(self.provenance.get("is_real_world_data", False))

    @property
    def identifier(self) -> str:
        return f"{self.name}@{self.version}"


def _require(manifest: dict[str, Any], key: str) -> Any:
    if key not in manifest:
        raise ArtifactError(f"model artifact manifest is missing {key!r}")
    return manifest[key]


def load_bundle(path: str | Path) -> ModelBundle:
    """Read a bundle from disk.

    Raises :class:`ArtifactError` rather than returning something half-formed:
    an application that starts with a broken model and silently serves nothing
    is worse than one that says why.
    """
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise ArtifactError(
            f"no model artifact at {artifact_path}. Run "
            "`python scripts/export_baseline_model.py` to train and export one."
        )

    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - joblib ships with scikit-learn
        raise ArtifactError("joblib is required to load a model artifact") from exc

    try:
        payload = joblib.load(artifact_path)
    except Exception as exc:
        raise ArtifactError(f"model artifact at {artifact_path} could not be read") from exc

    if not isinstance(payload, dict) or payload.get("bundle_format") != BUNDLE_FORMAT:
        raise ArtifactError(
            f"model artifact at {artifact_path} is not a CampusShield bundle "
            f"(expected bundle_format {BUNDLE_FORMAT})"
        )

    manifest = payload.get("manifest") or {}
    provenance = manifest.get("data_provenance")
    if not isinstance(provenance, dict) or not provenance:
        # The load-time refusal described above.
        raise ArtifactError(
            "model artifact carries no data_provenance. A model whose training "
            "data is unrecorded will not be served: its suggestions could not be "
            "described honestly to a responder."
        )

    estimator = payload.get("estimator")
    if estimator is None or not hasattr(estimator, "predict_proba"):
        raise ArtifactError(
            "model artifact has no estimator exposing predict_proba; confidence "
            "cannot be reported without it"
        )

    bundle = ModelBundle(
        estimator=estimator,
        name=_require(manifest, "name"),
        version=_require(manifest, "version"),
        family=_require(manifest, "family"),
        task=_require(manifest, "task"),
        classes=tuple(_require(manifest, "classes")),
        provenance=provenance,
        hyperparameters=manifest.get("hyperparameters") or {},
        headline_metrics=manifest.get("headline_metrics") or {},
        trained_at=manifest.get("trained_at"),
        training_rows=manifest.get("training_rows"),
    )

    logger.info(
        "loaded model artifact %s (%s, %d classes, provenance=%s)",
        bundle.identifier,
        bundle.family,
        len(bundle.classes),
        provenance.get("provenance", "unrecorded"),
    )
    if not bundle.is_real_world_trained:
        logger.warning(
            "MODEL TRAINED ON SYNTHETIC DATA — %s. Suggestions are decision "
            "support only and carry no real-world accuracy claim.",
            bundle.identifier,
        )
    return bundle


def save_bundle(path: str | Path, *, estimator: Any, manifest: dict[str, Any]) -> Path:
    """Write a bundle. Used by the export script, never by the application."""
    if not isinstance(manifest.get("data_provenance"), dict):
        raise ArtifactError("refusing to export a model artifact without data_provenance")

    import joblib

    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"bundle_format": BUNDLE_FORMAT, "estimator": estimator, "manifest": manifest},
        artifact_path,
        compress=3,
    )
    return artifact_path
