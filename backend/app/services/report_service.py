"""Report submission and retrieval.

This module owns every rule about how a report comes into existence and who may
see it afterwards.  It imports no Flask: everything it needs arrives as an
argument, which is what lets it be tested directly and keeps the HTTP layer to
parsing and serialising.

The privacy invariants enforced here, each also backed by a database constraint
or trigger so that a bug in this file cannot break them:

1. An anonymous report never receives an attribution row.
2. An anonymous report is never contactable.
3. An anonymous report never carries an original filename on its evidence.
4. A caller only ever sees a report the authorisation policy admits them to.
5. The one-time access token is returned once, at submission, and never stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..errors import (
    AuthorizationError,
    NotFoundError,
    QuotaExceededError,
    ValidationError,
)
from ..models import Report
from ..models.enums import (
    LocationSignalSource,
    ReporterRelationship,
    SubmissionMode,
)
from ..repositories.catalog_repository import (
    CategoryRepository,
    LocationRepository,
    PolicyRepository,
)
from ..repositories.evidence_repository import EvidenceRepository
from ..repositories.report_repository import ReportRepository, ReportTokenRepository
from ..security.authorization import (
    ReportAccessContext,
    can_create_report,
    can_view_narrative,
    can_view_report,
)
from ..security.principal import Principal
from ..utils.campus_time import campus_hour_and_dow, ensure_aware, reject_future
from ..utils.references import (
    generate_access_token,
    generate_public_ref,
    hash_access_token,
)
from .evidence_service import hash_token as hash_evidence_token
from .location_service import LocationResolver, LocationSignal
from .triage_service import TriageService

logger = logging.getLogger(__name__)

MAX_REF_ATTEMPTS = 5


@dataclass(slots=True)
class ReportSubmission:
    """A validated request to create a report."""

    category_id: int
    location_id: int
    occurred_at: datetime
    narrative: str
    anonymous: bool = False
    reporter_relationship: ReporterRelationship = ReporterRelationship.AFFECTED
    location_hint: str | None = None
    is_emergency: bool = False
    is_ongoing: bool = False
    contact_consent: bool = True
    # Opaque capability tokens from POST /evidence. NOT storage paths: a
    # client-supplied path would let a caller name an object it does not own,
    # which is the defect PHASE_4B_ARCHITECTURE.md §A identified in the previous
    # contract.
    evidence_tokens: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubmissionResult:
    report: Report
    access_token: str | None
    """Raw token, present only for anonymous submissions.

    Returned to the caller exactly once and never persisted — only its SHA-256
    reaches the database.  If the reporter loses it, nobody can recover it for
    them, which is the cost of there being no identity to fall back on.
    """


@dataclass(slots=True)
class ReportDetail:
    report: Report
    narrative: str | None
    narrative_available: bool
    narrative_withheld_reason: str | None
    evidence_count: int
    status_history: list


class ReportService:
    def __init__(
        self,
        *,
        reports: ReportRepository,
        evidence: EvidenceRepository,
        locations: LocationRepository,
        categories: CategoryRepository,
        policies: PolicyRepository,
        tokens: ReportTokenRepository,
        campus_timezone: str,
        max_evidence: int = 5,
        corroboration_radius_m: int = 150,
        triage: TriageService | None = None,
    ) -> None:
        self._reports = reports
        self._evidence = evidence
        self._locations = locations
        self._categories = categories
        self._policies = policies
        self._tokens = tokens
        self._campus_timezone = campus_timezone
        self._max_evidence = max_evidence
        self._resolver = LocationResolver(corroboration_radius_m=corroboration_radius_m)
        # Optional so the service is constructible without a model — tests that
        # care about reporting, and any deployment with no artifact, run without.
        self._triage = triage

    # -- creation ----------------------------------------------------------

    def submit(
        self, principal: Principal, submission: ReportSubmission, *, now: datetime | None = None
    ) -> SubmissionResult:
        if not can_create_report(principal):
            raise AuthorizationError("This account cannot file reports.")

        now = ensure_aware(now or datetime.now(timezone.utc))

        category = self._categories.get_active(submission.category_id)
        if category is None:
            raise ValidationError(
                "Unknown or inactive report category.",
                details={"fields": {"category_id": ["No such active category."]}},
            )

        location = self._locations.get_active(submission.location_id)
        if location is None:
            # Also the answer when a location exists but has not been surveyed:
            # an unverified location has no coordinates, so a report filed
            # against it could never appear on a map or feed hotspot detection.
            raise ValidationError(
                "Unknown or inactive campus location.",
                details={"fields": {"location_id": ["No such active location."]}},
            )

        reject_future(submission.occurred_at, now)

        if submission.is_emergency and not category.emergency_eligible:
            raise ValidationError(
                "This category cannot be raised as an emergency.",
                details={
                    "fields": {"is_emergency": [f"{category.label} is not emergency-eligible."]}
                },
            )
        if submission.is_ongoing and not submission.is_emergency:
            raise ValidationError(
                "is_ongoing may only be set on an emergency report.",
                details={"fields": {"is_ongoing": ["Requires is_emergency to be true."]}},
            )

        self._enforce_quota(principal, now)

        occurred_hour, occurred_dow = campus_hour_and_dow(
            submission.occurred_at, self._campus_timezone
        )
        mode = SubmissionMode.ANONYMOUS if submission.anonymous else SubmissionMode.IDENTIFIED

        report = Report(
            public_ref=self._allocate_public_ref(now),
            report_kind=category.kind,
            submission_mode=mode,
            reporter_relationship=submission.reporter_relationship,
            declared_category_id=category.category_id,
            location_id=location.location_id,
            location_hint=submission.location_hint,
            occurred_at=ensure_aware(submission.occurred_at),
            occurred_hour=occurred_hour,
            occurred_dow=occurred_dow,
            is_emergency=submission.is_emergency,
            is_ongoing=submission.is_ongoing,
            # Never set directly for an identified report either: a trigger
            # derives it from the attribution row's contact_consent, so there is
            # one source of truth for "can this person be reached".
            reporter_contactable=False,
        )
        self._reports.add(report)
        self._reports.add_narrative(report.report_id, submission.narrative)
        self._reports.record_initial_status(report.report_id)

        access_token: str | None = None
        if mode is SubmissionMode.IDENTIFIED:
            self._reports.attribute(
                report, principal.user_id, contact_consent=submission.contact_consent
            )
        else:
            raw = generate_access_token()
            ttl_days = self._policies.get_int("anonymous_token_ttl_days", 180)
            self._reports.add_access_token(
                report.report_id,
                hash_access_token(raw),
                now + timedelta(days=ttl_days),
            )
            access_token = raw

        signal = self._attach_evidence(report, submission.evidence_tokens)

        # The signal describes; it never decides. `report.location_id` was set
        # above from what the student chose and is not revisited here — the
        # resolver's output goes into a separate table whose whole purpose is
        # that it cannot be mistaken for the incident location.
        self._reports.set_location_detail(
            report.report_id, self._resolver.resolve(location, signal)
        )

        self._reports.increment_quota(principal.user_id, now.date())

        # Machine assistance, last and non-fatal. The report is complete and
        # valid at this point; triage adds a suggestion, a risk band and possible
        # links beside it. `run_for_report` swallows its own failures — a model
        # that will not load must never cost a student their report.
        if self._triage is not None:
            self._triage.run_for_report(report, submission.narrative, category)

        logger.info(
            "report %s created (mode=%s emergency=%s category=%s)",
            report.public_ref,
            mode.value,
            submission.is_emergency,
            category.code,
        )
        return SubmissionResult(report=report, access_token=access_token)

    def _attach_evidence(self, report: Report, tokens: list[str]) -> LocationSignal | None:
        """Claim staged uploads onto the report.

        Runs inside the report-creation transaction, so either the report and
        all of its evidence exist or neither does. A student is never told their
        report was submitted while an image silently went missing.

        Nothing about the reporter travels with the evidence. The pending row
        carries no user id, the storage path is server-generated and opaque, and
        no filename is persisted — for identified reports as well as anonymous
        ones.

        Returns the location signal carried by the first image that had one, for
        the resolver to compare against the selected campus location. Returns
        None when no image carried a coordinate, which is the ordinary case.
        """
        signal: LocationSignal | None = None

        if not tokens:
            return None
        if len(tokens) > self._max_evidence:
            raise ValidationError(
                f"At most {self._max_evidence} images per report.",
                details={"fields": {"evidence_tokens": ["Too many images."]}},
            )
        if len(set(tokens)) != len(tokens):
            raise ValidationError(
                "The same image was attached twice.",
                details={"fields": {"evidence_tokens": ["Duplicate image."]}},
            )

        for token in tokens:
            pending = self._evidence.get_pending(hash_evidence_token(token))
            if pending is None:
                # Unknown, already claimed, or expired — not distinguished, so a
                # caller cannot learn whether a token they do not hold is real.
                raise ValidationError(
                    "One of the attached images is no longer available. Please add it again.",
                    details={"fields": {"evidence_tokens": ["Unknown or expired image."]}},
                )
            self._evidence.attach(pending, report.report_id)

            # Take the first photograph that carried a coordinate. "First" rather
            # than "closest": picking the image that best agrees with the
            # selection would be choosing the answer that makes the report look
            # most consistent, which is precisely the reasoning a corroboration
            # check must not do.
            # Both or neither — a CHECK constraint pairs them, and testing both
            # here keeps that true for a reader as well as for the database.
            if (
                signal is None
                and pending.exif_latitude is not None
                and pending.exif_longitude is not None
            ):
                signal = LocationSignal(
                    latitude=float(pending.exif_latitude),
                    longitude=float(pending.exif_longitude),
                    source=LocationSignalSource.PHOTO_EXIF,
                    captured_at=pending.exif_captured_at,
                )

        return signal

    def _enforce_quota(self, principal: Principal, now: datetime) -> None:
        limit = self._policies.get_int("daily_report_quota", 5)
        used = self._reports.quota_used(principal.user_id, now.date())
        if used >= limit:
            raise QuotaExceededError(
                f"You have reached today's limit of {limit} reports.",
                details={"limit": limit, "used": used},
            )

    def _allocate_public_ref(self, now: datetime) -> str:
        for _ in range(MAX_REF_ATTEMPTS):
            candidate = generate_public_ref(now)
            if not self._reports.public_ref_exists(candidate):
                return candidate
        raise RuntimeError("could not allocate a unique public reference")

    # -- retrieval ---------------------------------------------------------

    def list_own_reports(
        self, principal: Principal, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Report], int]:
        """Reports this user filed under their own name.

        Anonymous submissions are absent, and that is the guarantee rather than a
        limitation: they are linked to nobody, so nothing — including this
        endpoint — can find them from a user id.  Anonymous reporters follow
        their case with the token issued at submission.
        """
        reports = self._reports.list_for_reporter(principal.user_id, limit=limit, offset=offset)
        total = self._reports.count_for_reporter(principal.user_id)
        return reports, total

    def get_report_detail(
        self,
        principal: Principal | None,
        *,
        public_ref: str,
        raw_token: str | None = None,
    ) -> ReportDetail:
        report = self._reports.get_by_public_ref(public_ref)

        token_valid = False
        if raw_token and report is not None:
            token = self._tokens.resolve(hash_access_token(raw_token))
            token_valid = token is not None and token.report_id == report.report_id
            if token_valid:
                self._tokens.record_use(hash_access_token(raw_token))

        if report is None:
            raise NotFoundError("No report exists with that reference.")

        ctx = self._reports.access_context(report, via_token=token_valid)
        if not can_view_report(principal, ctx):
            # 404, not 403.  Distinguishing "does not exist" from "exists but is
            # not yours" turns this endpoint into an oracle: a caller could
            # confirm a reference is real, and references appear on printed
            # acknowledgements and in screenshots.
            logger.info(
                "denied report access for %s to %s",
                principal.user_id if principal else "anonymous",
                public_ref,
            )
            raise NotFoundError("No report exists with that reference.")

        narrative_text: str | None = None
        withheld: str | None = None
        record = self._reports.get_narrative(report.report_id)

        if not can_view_narrative(principal, ctx):
            withheld = "not_authorised"
        elif record is None:
            withheld = "missing"
        elif record.narrative_purged_at is not None:
            withheld = "purged"
        else:
            narrative_text = record.narrative

        return ReportDetail(
            report=report,
            narrative=narrative_text,
            narrative_available=narrative_text is not None,
            narrative_withheld_reason=withheld,
            evidence_count=len(self._reports.list_evidence(report.report_id)),
            status_history=self._reports.visible_status_history(report.report_id),
        )

    def access_context_for(self, report: Report) -> ReportAccessContext:
        return self._reports.access_context(report)
