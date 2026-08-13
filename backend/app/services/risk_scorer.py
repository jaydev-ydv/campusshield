"""Triage prompting: how urgently should someone look at this report?

## What this is, and what it is not

It is a **rule-based ordering aid**. Every factor is a number a human chose, all
of them are visible in `factors`, and any responder can read why a report scored
what it did. It exists so that fourteen reports arriving overnight can be looked
at in a sensible order.

It is **not** a prediction of harm, not a measure of how serious an incident
really was, and not an assessment of anybody. It never decides anything: no
dispatch, no assignment, no status change, no notification. A responder reads it
and forms their own view.

## Why rules and not a model

A learned risk score needs outcome labels — reports where someone recorded what
actually happened afterwards. This project has none, and `ml/DATASET.md` explains
why none can be obtained legitimately right now. A model fitted on synthetic text
would produce a number with no relationship to real harm, and a number carries
authority that prose does not: "risk 78" gets acted on.

So the rules are explicit, few, and auditable. When real outcome data exists this
module is the place a learned scorer would replace them, and `scorer_version`
plus `model_id` on `core.risk_assessment` are already there for it.

## What may never be a factor

Anything about the reporter. Not their identity, not how many reports they have
filed, not `reporter_relationship`, not whether they were believed before.

`reporter_relationship` records **vantage point** — affected, witness, third
party — and treating "witness" as less urgent than "affected" would be a
credibility judgement wearing a metadata field's clothes. `core.risk_assessment`
enforces this with `ck_risk_factors_no_credibility_terms`, which rejects those
keys at both the top level and inside `weights`. :data:`BARRED_FACTOR_KEYS`
mirrors the constraint so a mistake fails in a test rather than at an insert.

The scorer is not given a reporter to read. That is the real guarantee: there is
no argument to misuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import Report, ReportCategory
from ..models.enums import RiskBand
from ..utils.campus_time import campus_hour_and_dow

# Bumped whenever a weight or rule changes, so an old assessment stays
# interpretable rather than silently meaning something else.
SCORER_VERSION = "rules-1.0.0"

# Mirrors ck_risk_factors_no_credibility_terms. Asserted equal to the database
# constraint by a test.
BARRED_FACTOR_KEYS = frozenset(
    {
        "reporter_relationship",
        "credibility",
        "credibility_score",
        "trust",
        "trust_score",
        "reliability",
        "reporter_score",
    }
)

# Points per factor. They sum to at most 100 by construction; the cap below is a
# guard, not a normalisation step that would make the weights unreadable.
WEIGHTS: dict[str, int] = {
    # A category's own severity, seeded per category in the database rather than
    # hard-coded here. Scaled from base_severity 1-5.
    "category_severity": 40,
    # The student pressed "this needs immediate attention". That they said so is
    # the strongest single signal available, and it is theirs, not inferred.
    "emergency": 25,
    # Still happening. The difference between a response and a report.
    "ongoing": 20,
    # Reported within the hour. Recency is about whether anyone can still act,
    # not about whether it is true.
    "recent": 5,
    # Occurred during hours when help is thinnest on the ground.
    "night_hours": 5,
    # Other open reports at the same location recently. A place, not a person.
    "location_repeat": 5,
}

# Night is 20:00-05:59 campus time. A blunt boundary, deliberately: a smooth
# curve here would imply a precision the reasoning does not have.
NIGHT_START_HOUR = 20
NIGHT_END_HOUR = 6

BAND_THRESHOLDS: tuple[tuple[int, RiskBand], ...] = (
    (75, RiskBand.CRITICAL),
    (50, RiskBand.HIGH),
    (25, RiskBand.MODERATE),
    (0, RiskBand.LOW),
)


@dataclass(frozen=True, slots=True)
class RiskResult:
    score: float
    band: RiskBand
    factors: dict[str, Any]
    scorer_version: str = SCORER_VERSION


def band_for(score: float) -> RiskBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return RiskBand.LOW  # pragma: no cover - the 0 threshold always matches


def score_report(
    report: Report,
    category: ReportCategory | None,
    *,
    campus_timezone: str,
    location_repeat_count: int = 0,
    now: datetime,
) -> RiskResult:
    """Score one report.

    Note the arguments: a report, its category, a timezone, and a count of other
    reports at the same place. **No principal, no attribution, no reporter.**
    The signature is the guarantee.
    """
    contributions: dict[str, float] = {}

    # Category severity, 1-5 from the database, scaled onto the weight.
    severity = category.base_severity if category else 3
    contributions["category_severity"] = round(
        WEIGHTS["category_severity"] * (max(1, min(5, severity)) / 5), 2
    )

    if report.is_emergency:
        contributions["emergency"] = float(WEIGHTS["emergency"])
    if report.is_ongoing:
        contributions["ongoing"] = float(WEIGHTS["ongoing"])

    age_minutes = max((now - report.submitted_at).total_seconds() / 60, 0)
    if age_minutes <= 60:
        contributions["recent"] = float(WEIGHTS["recent"])

    occurred_hour, _ = campus_hour_and_dow(report.occurred_at, campus_timezone)
    if occurred_hour >= NIGHT_START_HOUR or occurred_hour < NIGHT_END_HOUR:
        contributions["night_hours"] = float(WEIGHTS["night_hours"])

    if location_repeat_count > 0:
        # Capped at the weight: three prior reports and thirty should not differ
        # by six times the urgency, and a long tail here would let one busy
        # location dominate the queue.
        contributions["location_repeat"] = float(
            min(WEIGHTS["location_repeat"], location_repeat_count)
        )

    score = round(min(100.0, sum(contributions.values())), 2)

    factors: dict[str, Any] = {
        "contributions": contributions,
        "weights": dict(WEIGHTS),
        "inputs": {
            "category_code": category.code if category else None,
            "base_severity": severity,
            "is_emergency": report.is_emergency,
            "is_ongoing": report.is_ongoing,
            "occurred_hour_campus": occurred_hour,
            "age_minutes_at_scoring": round(age_minutes, 1),
            "other_open_reports_at_location": location_repeat_count,
        },
        "scorer": SCORER_VERSION,
        "method": "rule_based",
        "excluded_by_design": [
            "reporter identity",
            "reporter history",
            "how the reporter was involved",
            "narrative content",
        ],
        "caveat": (
            "Triage ordering only. Rule-based, not learned, and not validated "
            "against outcomes — no outcome data exists. Not a prediction of harm "
            "and not an assessment of any person."
        ),
    }

    _assert_no_credibility_terms(factors)
    return RiskResult(score=score, band=band_for(score), factors=factors)


def _assert_no_credibility_terms(factors: dict[str, Any]) -> None:
    """Fail here rather than at the INSERT.

    The database would reject the row anyway. Catching it in the application
    turns a constraint violation on a student's submission into a test failure on
    a developer's machine.
    """
    offending = BARRED_FACTOR_KEYS & set(factors)
    weights = factors.get("weights")
    if isinstance(weights, dict):
        offending |= BARRED_FACTOR_KEYS & set(weights)
    if offending:
        raise ValueError(f"risk factors must not contain credibility terms: {sorted(offending)}")
