"""Deterministic, stratified, duplicate-aware splitting.

The ordinary way to split a dataset is `train_test_split(..., stratify=y)`. That
is not sufficient here, because it splits *rows* and the unit that must not cross
the boundary is the *text*.

If the same narrative appears twice — two students reporting one event, a
template that fired identically twice, or the same report re-imported — and the
copies land either side of the split, the model memorises the answer during
training and is then rewarded for reciting it at test time. The metric that comes
out is not wrong by a little; it is measuring the wrong thing entirely.

So texts are grouped first and groups are assigned as units. Stratification is
then applied over groups rather than rows, which is why the realised split
proportions can differ slightly from the requested ones. That is the correct
trade: an exactly-20% test set with leakage in it is worth less than a 19.4% one
without.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .synthetic import Example

WORD = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class Split:
    train: list[Example] = field(default_factory=list)
    val: list[Example] = field(default_factory=list)
    test: list[Example] = field(default_factory=list)

    @property
    def sizes(self) -> dict[str, int]:
        return {"train": len(self.train), "val": len(self.val), "test": len(self.test)}

    def all_examples(self) -> list[Example]:
        return [*self.train, *self.val, *self.test]


def normalise_for_dedup(text: str) -> str:
    """Canonical form for duplicate detection.

    Case, punctuation and whitespace are removed so that two texts differing only
    in formatting are recognised as the same. This is stricter than the
    preprocessing used for modelling, on purpose: for leakage the question is
    "is this the same narrative?", not "does this mean the same thing?".
    """
    return " ".join(WORD.findall(text.lower()))


def text_hash(text: str) -> str:
    return hashlib.sha256(normalise_for_dedup(text).encode("utf-8")).hexdigest()[:16]


def shingles(text: str, size: int = 5) -> set[str]:
    tokens = normalise_for_dedup(text).split()
    if len(tokens) < size:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / (len(a) + len(b) - intersection)


def group_duplicates(
    examples: list[Example], *, near_duplicate_threshold: float = 0.95
) -> list[list[Example]]:
    """Group exact and near-duplicate texts so they can be split as a unit.

    Exact duplicates are grouped by hash — cheap and complete. Near-duplicates
    are then found by Jaccard similarity over 5-token shingles, compared only
    *within a label*: two texts with different gold labels are not duplicates in
    any sense that matters here, and comparing across labels would merge genuinely
    distinct examples.

    O(n²) within each label. At this corpus size that is milliseconds; a real
    corpus would want MinHash, and the interface would not change.
    """
    by_hash: dict[str, list[Example]] = defaultdict(list)
    for example in examples:
        by_hash[text_hash(example.text)].append(example)

    groups = list(by_hash.values())
    if near_duplicate_threshold >= 1.0:
        return groups

    by_label: dict[str, list[list[Example]]] = defaultdict(list)
    for group in groups:
        by_label[group[0].label].append(group)

    merged: list[list[Example]] = []
    for label_groups in by_label.values():
        signatures = [shingles(g[0].text) for g in label_groups]
        parent = list(range(len(label_groups)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(label_groups)):
            for j in range(i + 1, len(label_groups)):
                if jaccard(signatures[i], signatures[j]) >= near_duplicate_threshold:
                    parent[find(i)] = find(j)

        clusters: dict[int, list[Example]] = defaultdict(list)
        for i, group in enumerate(label_groups):
            clusters[find(i)].extend(group)
        merged.extend(clusters.values())

    return merged


def stratified_group_split(
    examples: list[Example],
    *,
    test_size: float,
    val_size: float,
    seed: int,
    deduplicate: bool = True,
    near_duplicate_threshold: float = 0.95,
) -> Split:
    """Split into train/val/test, assigning duplicate groups as units.

    Deterministic: the same examples and seed always produce the same split.
    """
    if not 0 < test_size < 1 or not 0 <= val_size < 1 or test_size + val_size >= 1:
        raise ValueError("test_size and val_size must leave a non-empty training set")

    groups = (
        group_duplicates(examples, near_duplicate_threshold=near_duplicate_threshold)
        if deduplicate
        else [[e] for e in examples]
    )

    # Stratify by the group's label. A group is single-label by construction:
    # near-duplicates are only merged within a label.
    by_label: dict[str, list[list[Example]]] = defaultdict(list)
    for group in groups:
        by_label[group[0].label].append(group)

    split = Split()
    rng = random.Random(seed)

    for label in sorted(by_label):
        label_groups = sorted(by_label[label], key=lambda g: g[0].example_id or g[0].text)
        rng.shuffle(label_groups)

        n = len(label_groups)
        n_test = max(1, round(n * test_size)) if n > 2 else (1 if n > 1 else 0)
        n_val = max(1, round(n * val_size)) if n - n_test > 2 else 0

        for group in label_groups[:n_test]:
            split.test.extend(group)
        for group in label_groups[n_test : n_test + n_val]:
            split.val.extend(group)
        for group in label_groups[n_test + n_val :]:
            split.train.extend(group)

    for bucket in (split.train, split.val, split.test):
        bucket.sort(key=lambda e: e.example_id or e.text)

    return split
