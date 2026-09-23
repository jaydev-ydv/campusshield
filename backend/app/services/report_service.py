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
from ..models import CampusLocation, Report
from ..models.enums import (
    LocationSignalSource,
    ReporterRelationship,
    ReportKind,
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
    can_attach_evidence,
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
from .location_service import LocationResolver, LocationSignal, nearest_verified_location
from .triage_service import TriageService

logger = logging.getLogger(__name__)

MAX_REF_ATTEMPTS = 5

# The permanent core.campus_location row an emergency report anchors to when no
# location can be resolved. Seeded by migration 0007 and never offered to a
# student as a selectable location (is_active is FALSE by design).
EMERGENCY_SENTINEL_LOCATION_CODE = "SYS-UNSPECIFIED"

# The permanent core.report_category row every emergency report is created
# with. Seeded by migration 0007, routed to security. An SOS trigger has no
# time to classify itself, and a report without SOME category would be
# invisible to every responder queue (IncidentRepository._visible_to inner
# joins on it) — see submit_sos for the full reasoning.
EMERGENCY_SENTINEL_CATEGORY_CODE = "SOS_EMERGENCY"

# How long after one emergency report a second from the same reporter is
# treated as a duplicate press rather than a new event.
EMERGENCY_DEDUP_WINDOW = timedelta(seconds=120)

# Safety margin subtracted from `occurred_at` in submit_sos — see the comment
# at its use site.
EMERGENCY_OCCURRED_AT_MARGIN = timedelta(seconds=5)

EMERGENCY_NARRATIVE_PLACEHOLDER = (
    "Emergency SOS triggered by the reporter. No further details were "
    "provided at the time of the alert."
)


@dataclass(slots=True)
class ReportSubmission:
    """A validated request to create a report."""

    location_id: int
    occurred_at: datetime
    narrative: str
    # None only for an emergency report: the normal flow still requires a
    # category (enforced below, and by CreateReportSchema before that), but an
    # SOS trigger has no time to classify itself and a responder can set this
    # during investigation via IncidentService.override_category.
    category_id: int | None = None
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
    # A browser geolocation reading, used only when no photo evidence carried
    # its own EXIF coordinate (see `_select_signal`). Never set by the normal
    # report form — only the emergency path collects a device position.
    device_location: LocationSignal | None = None


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
        """The normal, client-driven creation path.

        ``location_id`` must name an active (verified) location — the one
        guarantee that lets every report feed the map and hotspot detection.
        This check is what the emergency path in :meth:`submit_sos` exists to
        route around *for the one, server-chosen sentinel location only* — see
        that method's docstring for why bypassing it there does not weaken
        this one.
        """
        if not can_create_report(principal):
            raise AuthorizationError("This account cannot file reports.")

        location = self._locations.get_active(submission.location_id)
        if location is None:
            # Also the answer when a location exists but has not been surveyed:
            # an unverified location has no coordinates, so a report filed
            # against it could never appear on a map or feed hotspot detection.
            raise ValidationError(
                "Unknown or inactive campus location.",
                details={"fields": {"location_id": ["No such active location."]}},
            )
        return self._create(principal, submission, location, now=now)

    def submit_sos(
        self,
        principal: Principal,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        reporter_relationship: ReporterRelationship = ReporterRelationship.AFFECTED,
        now: datetime | None = None,
    ) -> SubmissionResult:
        """Create an emergency report from nothing but who is asking.

        No classification choice, no narrative, no location choice, no
        evidence — every one of those can be corrected or added afterwards
        (category via ``IncidentService.override_category`` — every report
        this creates starts filed under the fixed "Emergency SOS" category,
        not because a responder should treat every SOS identically, but
        because ``IncidentRepository._visible_to`` inner-joins
        ``report_category`` to find who a report routes to: a report with no
        category at all would be invisible to every responder queue,
        defeating the point of the alert. Evidence via a follow-up
        ``POST /reports/<ref>/evidence``). ``latitude``/``longitude`` are a
        best-effort browser reading and may be absent entirely; this never
        raises for that reason.

        **Always identified, never anonymous.** Unlike the normal flow, the
        person this creates a record for is (by construction of this being an
        SOS) the one who may need to be reached. Anonymous SOS is real future
        scope, deliberately not built now: doing it safely needs its own
        answer to rate-limiting an unlinkable submitter, which the normal
        flow's ``identity.submission_quota`` design solves *because* every
        submission is tied to a user id first and stripped of identity
        second — a shortcut here would either weaken that or invent a second,
        untested anonymity mechanism under this feature's own deadline.

        **Bypassing the active-location check, safely.** ``submit()`` requires
        an active location because that field is client-supplied — trusting it
        blindly would let any report claim to be anywhere. Here the location is
        never client-supplied: it is either the emergency sentinel row (fixed,
        inactive by design so it can never be chosen any other way) or a
        location this method itself found via
        :func:`nearest_verified_location`, which only ever searches
        :meth:`LocationRepository.list_active` — already active by
        construction. Nothing this method passes to ``_create`` can be a
        location a caller chose.
        """
        if not can_create_report(principal):
            raise AuthorizationError("This account cannot file reports.")

        now = ensure_aware(now or datetime.now(timezone.utc))

        # Duplicate-press protection. A second SOS from the same person a few
        # seconds or minutes later is far likelier to be a repeated tap (a
        # double press, a retry after a slow response) than a second, distinct
        # emergency — but a genuinely new one occurring later must still go
        # through, so the window is short and the check is on the *reporter's
        # own* most recent report only.
        recent = self._reports.list_for_reporter(principal.user_id, limit=1, offset=0)
        if (
            recent
            and recent[0].is_emergency
            and (now - ensure_aware(recent[0].submitted_at)) < EMERGENCY_DEDUP_WINDOW
        ):
            return SubmissionResult(report=recent[0], access_token=None)

        location, signal = self._resolve_emergency_location(latitude, longitude, now)
        category = self._categories.get_by_code(EMERGENCY_SENTINEL_CATEGORY_CODE)
        if category is None:
            # Same guarantee as the location sentinel: migration 0007 seeds
            # this row in every migrated environment.
            raise RuntimeError(
                f"emergency sentinel category {EMERGENCY_SENTINEL_CATEGORY_CODE!r} is missing; "
                "has migration 0007 been applied?"
            )

        submission = ReportSubmission(
            location_id=location.location_id,
            # A hair before `now`, deliberately: `ck_report_occurred_not_future`
            # requires occurred_at <= submitted_at, and submitted_at is a
            # database server_default evaluated when the row is actually
            # written — a moment that is never provably >= this application's
            # clock reading, especially under connection pooling or a
            # long-running transaction. `reject_future`'s own 120-second
            # tolerance exists for the same class of clock-skew problem; this
            # is that same margin applied to a timestamp this service
            # generates itself rather than one a client supplied.
            occurred_at=now - EMERGENCY_OCCURRED_AT_MARGIN,
            narrative=EMERGENCY_NARRATIVE_PLACEHOLDER,
            category_id=category.category_id,
            anonymous=False,
            reporter_relationship=reporter_relationship,
            is_emergency=True,
            is_ongoing=False,
            contact_consent=True,
            device_location=signal,
        )
        return self._create(principal, submission, location, now=now)

    def _resolve_emergency_location(
        self, latitude: float | None, longitude: float | None, now: datetime
    ) -> tuple[CampusLocation, LocationSignal | None]:
        """The location an SOS anchors to, and the signal that justifies it.

        A device position that lands within `nearest_verified_location`'s
        radius of a real, surveyed place is used directly, so the incident
        gets a named location a responder can act on. Anything else —
        permission denied, no fix in time, or a position nothing surveyed is
        near — falls back to the sentinel. Today that fallback is the only
        outcome that ever happens: zero locations are verified yet.
        """
        signal: LocationSignal | None = None
        if latitude is not None and longitude is not None:
            signal = LocationSignal(
                latitude=latitude,
                longitude=longitude,
                source=LocationSignalSource.DEVICE_GPS,
                captured_at=now,
            )
            matched = nearest_verified_location(latitude, longitude, self._locations.list_active())
            if matched is not None:
                return matched, signal

        sentinel = self._locations.get_by_code(EMERGENCY_SENTINEL_LOCATION_CODE)
        if sentinel is None:
            # Migration 0007 guarantees this row exists in every migrated
            # environment. Its absence means the environment is not fully
            # migrated, not that the emergency happened somewhere unusual.
            raise RuntimeError(
                f"emergency sentinel location {EMERGENCY_SENTINEL_LOCATION_CODE!r} is missing; "
                "has migration 0007 been applied?"
            )
        return sentinel, signal

    def _create(
        self,
        principal: Principal,
        submission: ReportSubmission,
        location: CampusLocation,
        *,
        now: datetime | None = None,
    ) -> SubmissionResult:
        """The creation logic shared by :meth:`submit` and :meth:`submit_sos`.

        Takes an already-resolved ``location`` rather than looking one up, so
        the one thing that differs between the two callers — how the location
        was chosen and validated — is decided entirely by the caller.
        """
        now = ensure_aware(now or datetime.now(timezone.utc))

        category = None
        if submission.category_id is not None:
            category = self._categories.get_active(submission.category_id)
            if category is None:
                raise ValidationError(
                    "Unknown or inactive report category.",
                    details={"fields": {"category_id": ["No such active category."]}},
                )
        elif not submission.is_emergency:
            # The schema for the normal endpoint already requires category_id;
            # this is a service-layer backstop so nothing can reach a
            # categoryless, non-emergency report by constructing the
            # dataclass directly.
            raise ValidationError(
                "category_id is required unless the report is an emergency.",
                details={"fields": {"category_id": ["Required."]}},
            )

        reject_future(submission.occurred_at, now)

        if submission.is_emergency and category is not None and not category.emergency_eligible:
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

        # A genuine emergency must never be blocked by a quota exhausted by
        # earlier, unrelated reports filed the same day.
        if not submission.is_emergency:
            self._enforce_quota(principal, now)

        occurred_hour, occurred_dow = campus_hour_and_dow(
            submission.occurred_at, self._campus_timezone
        )
        mode = SubmissionMode.ANONYMOUS if submission.anonymous else SubmissionMode.IDENTIFIED

        report = Report(
            public_ref=self._allocate_public_ref(now),
            report_kind=category.kind if category is not None else ReportKind.INCIDENT,
            submission_mode=mode,
            reporter_relationship=submission.reporter_relationship,
            declared_category_id=category.category_id if category is not None else None,
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

        # A photo's own EXIF coordinate takes precedence when both exist: it
        # was captured at the scene, whereas a device position is read at
        # submission time and may already be somewhere else. Neither is
        # collected for the normal flow's evidence-less case, and the normal
        # flow never sets device_location, so this is a no-op for it.
        signal = self._attach_evidence(report, submission.evidence_tokens)
        if signal is None:
            signal = submission.device_location

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
            category.code if category is not None else "none",
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

    def get_report_for_reporter(self, principal: Principal | None, *, public_ref: str) -> Report:
        """The report, only if the caller is the one who filed it.

        For actions that mutate a report after creation — today, only
        attaching evidence — where ``can_view_report``'s wider audience
        (assigned staff, admin, a token holder) is deliberately too broad: see
        :func:`can_attach_evidence`.
        """
        report = self._reports.get_by_public_ref(public_ref)
        if report is None:
            raise NotFoundError("No report exists with that reference.")

        ctx = self._reports.access_context(report)
        if not can_attach_evidence(principal, ctx):
            # Same 404-not-403 reasoning as get_report_detail: a 403 would
            # confirm the reference is real.
            logger.info(
                "denied evidence-attach access for %s to %s",
                principal.user_id if principal else "anonymous",
                public_ref,
            )
            raise NotFoundError("No report exists with that reference.")
        return report
