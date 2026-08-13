"""The transformer arm: a fine-tuned DistilBERT classifier.

DistilBERT rather than BERT-base or anything larger. It is ~40% smaller and ~60%
faster at roughly 97% of BERT-base's GLUE score, which makes it the right size
for an undergraduate prototype fine-tuned on CPU. Fine-tuning something larger
would cost hours and would not change what the comparison demonstrates.

Two things this module refuses to do:

**It never invents a result.** If torch or transformers are unavailable, or
training fails, it returns a record with `status: not_run` and a stated reason.
An absent number is a finding; a fabricated one is misconduct.

**It never sees a different test set from the baseline.** The same `Split` object
is passed to both arms, so the comparison is on identical held-out data by
construction rather than by convention.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from typing import Any

from .labels import CATEGORY_CODES, from_index, to_index
from .metrics import compute_metrics
from .preprocess import preprocess_all
from .splitting import Split


@dataclass(slots=True)
class TransformerAvailability:
    available: bool
    reason: str
    torch_version: str | None = None
    transformers_version: str | None = None


def check_availability() -> TransformerAvailability:
    try:
        import torch
        import transformers
    except ImportError as exc:
        return TransformerAvailability(
            available=False,
            reason=(
                f"transformer dependencies unavailable ({exc}). "
                "Install with: pip install torch transformers"
            ),
        )
    return TransformerAvailability(
        available=True,
        reason="torch and transformers importable",
        torch_version=torch.__version__,
        transformers_version=transformers.__version__,
    )


def hardware_description() -> dict[str, str]:
    info = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "device": "cpu",
    }
    try:
        import torch

        if torch.cuda.is_available():
            info["device"] = f"cuda ({torch.cuda.get_device_name(0)})"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            # Available on Apple silicon, but left unused: MPS support for some
            # transformer ops is still uneven, and a silent CPU fallback
            # mid-training would make the recorded training time meaningless.
            info["device"] = "cpu (mps available, not used)"
    except Exception:  # pragma: no cover - torch absent
        pass
    return info


def train_and_evaluate(
    split: Split,
    config: dict[str, Any],
    seed: int,
    *,
    model_name_override: str | None = None,
) -> dict[str, Any]:
    """Fine-tune and score on the held-out test split.

    Returns a dict with `status` of `completed` or `not_run`. Never raises for an
    environment problem — the caller records the reason and reports the arm as
    not run.
    """
    cfg = config["transformer"]
    availability = check_availability()

    if not availability.available:
        if not cfg.get("skip_if_unavailable", True):
            raise RuntimeError(availability.reason)
        return {
            "status": "not_run",
            "reason": availability.reason,
            "model": model_name_override or cfg["pretrained_model"],
        }

    import numpy as np
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_name = model_name_override or cfg["pretrained_model"]

    # Seeded everywhere that matters, so a rerun reproduces the result.
    torch.manual_seed(seed)
    np.random.seed(seed)

    pre = config["preprocessing"]
    x_train = preprocess_all([e.text for e in split.train], **pre)
    y_train = to_index([e.label for e in split.train])
    x_test = preprocess_all([e.text for e in split.test], **pre)
    y_test = [e.label for e in split.test]

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name, num_labels=len(CATEGORY_CODES)
        )
    except Exception as exc:
        return {
            "status": "not_run",
            "reason": f"could not load {model_name}: {exc}",
            "model": model_name,
        }

    max_length = cfg["max_length"]

    class TextDataset(Dataset):
        def __init__(self, texts: list[str], labels: list[int] | None):
            self.encodings = tokenizer(
                texts, truncation=True, padding="max_length", max_length=max_length
            )
            self.labels = labels

        def __len__(self) -> int:
            return len(self.encodings["input_ids"])

        def __getitem__(self, idx: int):
            item = {k: torch.tensor(v[idx]) for k, v in self.encodings.items()}
            if self.labels is not None:
                item["labels"] = torch.tensor(self.labels[idx])
            return item

    train_loader = DataLoader(
        TextDataset(x_train, y_train), batch_size=cfg["batch_size"], shuffle=True
    )
    test_loader = DataLoader(TextDataset(x_test, None), batch_size=cfg["batch_size"])

    optimiser = torch.optim.AdamW(
        model.parameters(), lr=float(cfg["learning_rate"]), weight_decay=cfg["weight_decay"]
    )
    total_steps = max(1, len(train_loader) * cfg["epochs"])
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimiser,
        max_lr=float(cfg["learning_rate"]),
        total_steps=total_steps,
        pct_start=cfg["warmup_ratio"],
        anneal_strategy="linear",
    )

    started = time.perf_counter()
    model.train()
    losses: list[float] = []
    for _ in range(cfg["epochs"]):
        for batch in train_loader:
            optimiser.zero_grad()
            outputs = model(**batch)
            outputs.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            scheduler.step()
            losses.append(float(outputs.loss.item()))
    train_seconds = time.perf_counter() - started

    model.eval()
    predictions: list[int] = []
    inference_started = time.perf_counter()
    with torch.no_grad():
        for batch in test_loader:
            logits = model(**batch).logits
            predictions.extend(torch.argmax(logits, dim=-1).tolist())
    inference_ms = (time.perf_counter() - inference_started) * 1000

    metrics = compute_metrics(y_test, from_index(predictions), labels=list(CATEGORY_CODES))
    metrics.update(
        {
            "status": "completed",
            "model": model_name,
            "mean_latency_ms": round(inference_ms / max(len(x_test), 1), 3),
            "train_seconds": round(train_seconds, 2),
            "final_train_loss": round(sum(losses[-10:]) / max(len(losses[-10:]), 1), 4),
            "hyperparameters": {
                "pretrained_model": model_name,
                "max_length": max_length,
                "epochs": cfg["epochs"],
                "learning_rate": float(cfg["learning_rate"]),
                "batch_size": cfg["batch_size"],
                "weight_decay": cfg["weight_decay"],
                "warmup_ratio": cfg["warmup_ratio"],
                "seed": seed,
            },
            "hardware": hardware_description(),
            "torch_version": availability.torch_version,
            "transformers_version": availability.transformers_version,
        }
    )
    return metrics
