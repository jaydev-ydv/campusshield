"""Data leakage detection.

The pipeline runs these before training and **aborts** on any failure rather than
warning. A leaked split does not produce a slightly optimistic number; it
produces a number that is measuring memorisation, and reporting it in a
dissertation as an accuracy figure would be a false claim.

Five checks:

1. **Exact text overlap** between train and test.
2. **Near-duplicate overlap**, which the hash check misses.
3. **Example-id overlap**, catching the same source example in two splits.
4. **Empty or degenerate splits**, where a metric would be meaningless.
5. **Label coverage**, so no test class is unseen in training.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .splitting import Split, jaccard, shingles, text_hash


@dataclass(slots=True)
class LeakageFinding:
    check: str
    passed: bool
    detail: str
    offending: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LeakageReport:
    findings: list[LeakageFinding]

    @property
    def passed(self) -> bool:
        return all(f.passed for f in self.findings)

    def failures(self) -> list[LeakageFinding]:
        return [f for f in self.findings if not f.passed]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "check": f.check,
                    "passed": f.passed,
                    "detail": f.detail,
                    # Capped: a failure report should be readable, and the first
                    # few offenders are enough to diagnose the cause.
                    "offending_sample": f.offending[:5],
                }
                for f in self.findings
            ],
        }


class LeakageError(RuntimeError):
    """Raised when a split cannot produce a trustworthy metric."""


def check_exact_overlap(split: Split) -> LeakageFinding:
    train = {text_hash(e.text) for e in split.train}
    val = {text_hash(e.text) for e in split.val}
    test = {text_hash(e.text) for e in split.test}

    train_test = train & test
    train_val = train & val
    val_test = val & test
    total = len(train_test) + len(train_val) + len(val_test)

    return LeakageFinding(
        check="exact_text_overlap",
        passed=total == 0,
        detail=(
            "No identical text appears in more than one split."
            if total == 0
            else f"{len(train_test)} train/test, {len(train_val)} train/val, "
            f"{len(val_test)} val/test identical texts"
        ),
        offending=sorted(train_test | train_val | val_test),
    )


def check_near_duplicate_overlap(split: Split, *, threshold: float = 0.95) -> LeakageFinding:
    """Catch paraphrase-level overlap the hash check cannot see.

    Compared within a label only: two texts with different gold labels are not
    duplicates, and cross-label comparison would flag legitimate examples.
    """
    train_by_label: dict[str, list[set[str]]] = {}
    for example in split.train:
        train_by_label.setdefault(example.label, []).append(shingles(example.text))

    offending: list[str] = []
    for example in split.test:
        candidate = shingles(example.text)
        for train_sig in train_by_label.get(example.label, []):
            if jaccard(candidate, train_sig) >= threshold:
                offending.append(example.example_id or example.text[:60])
                break

    return LeakageFinding(
        check="near_duplicate_overlap",
        passed=not offending,
        detail=(
            f"No test example is a near-duplicate (Jaccard >= {threshold}) of a training example."
            if not offending
            else f"{len(offending)} test examples closely match a training example"
        ),
        offending=offending,
    )


def check_id_overlap(split: Split) -> LeakageFinding:
    train = {e.example_id for e in split.train if e.example_id}
    val = {e.example_id for e in split.val if e.example_id}
    test = {e.example_id for e in split.test if e.example_id}
    overlap = (train & test) | (train & val) | (val & test)

    return LeakageFinding(
        check="example_id_overlap",
        passed=not overlap,
        detail=(
            "No example id appears in more than one split."
            if not overlap
            else f"{len(overlap)} example ids appear in multiple splits"
        ),
        offending=sorted(overlap),
    )


def check_split_sizes(split: Split, *, min_test: int = 10) -> LeakageFinding:
    sizes = split.sizes
    ok = sizes["train"] > 0 and sizes["test"] >= min_test
    return LeakageFinding(
        check="split_sizes",
        passed=ok,
        detail=(
            f"train={sizes['train']} val={sizes['val']} test={sizes['test']}"
            if ok
            else f"degenerate split: {sizes} (test must be >= {min_test})"
        ),
    )


def check_label_coverage(split: Split) -> LeakageFinding:
    train_labels = {e.label for e in split.train}
    test_labels = {e.label for e in split.test}
    unseen = sorted(test_labels - train_labels)

    return LeakageFinding(
        check="label_coverage",
        passed=not unseen,
        # Not leakage in the strict sense, but the same class of problem: a
        # metric that cannot mean what it appears to. A class present at test
        # time and absent at training time scores zero for reasons that have
        # nothing to do with the model.
        detail=(
            f"All {len(test_labels)} test labels appear in training."
            if not unseen
            else f"{len(unseen)} test labels never appear in training: {unseen}"
        ),
        offending=unseen,
    )


def run_all_checks(split: Split, *, near_duplicate_threshold: float = 0.95) -> LeakageReport:
    return LeakageReport(
        findings=[
            check_split_sizes(split),
            check_exact_overlap(split),
            check_near_duplicate_overlap(split, threshold=near_duplicate_threshold),
            check_id_overlap(split),
            check_label_coverage(split),
        ]
    )


def assert_no_leakage(split: Split, *, near_duplicate_threshold: float = 0.95) -> LeakageReport:
    """Run the checks and abort on failure.

    Deliberately fatal. A pipeline that warns about leakage and trains anyway
    produces a results file that looks exactly like a clean one.
    """
    report = run_all_checks(split, near_duplicate_threshold=near_duplicate_threshold)
    if not report.passed:
        lines = [f"  - {f.check}: {f.detail}" for f in report.failures()]
        raise LeakageError(
            "Refusing to train: the split would not produce a trustworthy metric.\n"
            + "\n".join(lines)
        )
    return report
