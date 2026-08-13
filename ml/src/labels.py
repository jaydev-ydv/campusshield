"""The label space, and the mapping from external datasets onto it.

The target labels are CampusShield's controlled report categories — the same
`core.report_category.code` values the production database holds, not a parallel
taxonomy invented for the models. Anything the classifier predicts has to be
something a student could have chosen on the form.

The mappings here are deliberately incomplete. Where an external label has no
honest CampusShield equivalent, it is recorded as unmapped rather than forced
into the nearest-looking category: a wrong mapping does not fail loudly, it
quietly trains the model to be wrong and then reports a confident metric about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReportKind(str, Enum):
    INCIDENT = "incident"
    CONCERN = "concern"


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    label: str
    kind: ReportKind


# Mirrors sql/seed_report_categories.sql. A test asserts the two agree, so this
# cannot drift from the database without something failing.
CATEGORIES: tuple[Category, ...] = (
    # Incidents
    Category("HARASS_VERBAL", "Verbal harassment or catcalling", ReportKind.INCIDENT),
    Category("HARASS_PHYSICAL", "Unwanted physical contact", ReportKind.INCIDENT),
    Category("STALKING", "Following or stalking", ReportKind.INCIDENT),
    Category("HARASS_DIGITAL", "Online or phone harassment", ReportKind.INCIDENT),
    Category("INTIMIDATION", "Threats or intimidation", ReportKind.INCIDENT),
    Category("RAGGING", "Ragging or hazing", ReportKind.INCIDENT),
    Category("VOYEURISM", "Filming or photography without consent", ReportKind.INCIDENT),
    Category("TRESPASS", "Unauthorised person on campus", ReportKind.INCIDENT),
    Category("OTHER_INCIDENT", "Other safety incident", ReportKind.INCIDENT),
    # Concerns
    Category("LIGHTING_POOR", "Poor or broken lighting", ReportKind.CONCERN),
    Category("ISOLATED_AREA", "Isolated or unsafe area", ReportKind.CONCERN),
    Category("CCTV_GAP", "No camera coverage where expected", ReportKind.CONCERN),
    Category("BLOCKED_ROUTE", "Blocked or unsafe walkway", ReportKind.CONCERN),
    Category("ACCESS_CONTROL", "Gate or door left unsecured", ReportKind.CONCERN),
    Category("TRANSPORT_SAFETY", "Bus stop or transport safety concern", ReportKind.CONCERN),
    Category("OTHER_CONCERN", "Other safety concern", ReportKind.CONCERN),
)

CATEGORY_CODES: tuple[str, ...] = tuple(c.code for c in CATEGORIES)
BY_CODE: dict[str, Category] = {c.code: c for c in CATEGORIES}


@dataclass(frozen=True, slots=True)
class LabelMapping:
    """One external label's relationship to a CampusShield category."""

    external_label: str
    campusshield_code: str | None
    quality: str  # 'exact' | 'close' | 'poor' | 'unmapped'
    rationale: str


# ---------------------------------------------------------------------------
# SafeCity (Karlekar & Bansal, EMNLP 2018)
#
# Recorded for completeness and to make the coverage gap concrete. The dataset
# is NOT used: it carries no licence and requires prior permission that has not
# been obtained. See DATASET.md §2.1.
# ---------------------------------------------------------------------------

SAFECITY_MAPPING: tuple[LabelMapping, ...] = (
    LabelMapping(
        "Touching/Groping",
        "HARASS_PHYSICAL",
        "exact",
        "Unwanted physical contact. The two definitions describe the same act.",
    ),
    LabelMapping(
        "Commenting",
        "HARASS_VERBAL",
        "exact",
        "Verbal harassment and catcalling. Direct correspondence.",
    ),
    LabelMapping(
        "Ogling/Facial Expressions/Staring",
        None,
        "unmapped",
        "No honest equivalent exists. It is non-verbal, so HARASS_VERBAL is "
        "wrong; nothing is recorded, so VOYEURISM is wrong. Mapping it to "
        "OTHER_INCIDENT would preserve the example while teaching the model "
        "nothing, so it is left unmapped and the loss is reported.",
    ),
)


def safecity_coverage() -> dict[str, object]:
    """How much of the label space SafeCity would cover, if permitted.

    Called by the evaluation report so the 12.5% figure is computed from the
    mapping rather than asserted in prose that could go stale.
    """
    mapped = {m.campusshield_code for m in SAFECITY_MAPPING if m.campusshield_code}
    return {
        "covered_categories": sorted(mapped),
        "covered_count": len(mapped),
        "total_categories": len(CATEGORIES),
        "coverage_fraction": round(len(mapped) / len(CATEGORIES), 4),
        "uncovered_categories": sorted(set(CATEGORY_CODES) - mapped),
        "unmapped_external_labels": [
            m.external_label for m in SAFECITY_MAPPING if m.campusshield_code is None
        ],
    }


def to_index(codes: list[str]) -> list[int]:
    """Stable label -> integer index, for the model layer."""
    lookup = {code: i for i, code in enumerate(CATEGORY_CODES)}
    unknown = sorted({c for c in codes if c not in lookup})
    if unknown:
        raise ValueError(f"labels outside the controlled category set: {unknown}")
    return [lookup[c] for c in codes]


def from_index(indices: list[int]) -> list[str]:
    return [CATEGORY_CODES[i] for i in indices]
