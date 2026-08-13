"""The responder plane: incident queue, incident detail, dispatch, destination.

This is the half of the product that turns a report into something a human acts
on. A student files; a responder reads, decides, goes, and records what happened.

Three rules hold throughout.

**Reach the incident, never the reporter.** Every method here works from
`core.report.location_id` — a controlled campus location. Nothing in this module
can resolve a reporter's identity, because nothing here queries
`identity.report_attribution`. For an anonymous report there is nothing to
resolve: the row does not exist.

**The queue is ordered by the incident.** Emergency, then ongoing, then recency.
Not by who reported it, not by how they were involved, not by how many reports
they have filed. `reporter_relationship` records vantage point and is barred from
credibility use by CHECK constraint in `core.risk_assessment`; the same rule holds
here, where no constraint could enforce it.

**Dispatch state moves forward only.** The transitions below are the enum from
`0001`, not a new vocabulary. A responder cannot un-arrive.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from ..models import CampusLocation, CaseStatusHistory, EmergencyDispatch
from ..models.enums import CoordinateStatus, DispatchState, ReportStatus, UserRole
from ..repositories.incident_repository import IncidentRepository, IncidentRow
from ..repositories.report_repository import ReportRepository
from ..security.authorization import can_view_narrative, can_view_report
from ..security.principal import Principal
from .case_service import AssignmentView, CaseService
from .triage_service import TriageService, TriageView

logger = logging.getLogger(__name__)


# The state machine, spelled out. Each key is a state; each value is what may
# follow it. Read top to bottom this is the responder's actual working sequence:
# a dispatch is raised, someone takes it, they set off, they arrive, it ends.
#
# `stood_down` is reachable from anywhere before arrival, because the reason to
# stand a dispatch down — it turned out not to be needed — can appear at any
# point. It is not a step towards `closed`: a dispatch that was stood down never
# reached a scene, and merging the two would lose that distinction from the
# record.
DISPATCH_TRANSITIONS: dict[DispatchState, frozenset[DispatchState]] = {
    DispatchState.PENDING: frozenset({DispatchState.ACKNOWLEDGED, DispatchState.STOOD_DOWN}),
    DispatchState.ACKNOWLEDGED: frozenset({DispatchState.DISPATCHED, DispatchState.STOOD_DOWN}),
    DispatchState.DISPATCHED: frozenset({DispatchState.ON_SCENE, DispatchState.STOOD_DOWN}),
    DispatchState.ON_SCENE: frozenset({DispatchState.CLOSED}),
    DispatchState.STOOD_DOWN: frozenset(),
    DispatchState.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Destination:
    """Where a responder is being sent.

    Always the student's selected campus location. Never a coordinate derived
    from a photograph, and never the reporter's device position — those are
    corroborating signals, and navigating to one would mean sending a responder
    to wherever a picture happened to be taken.

    `latitude`/`longitude` are None until the campus survey provides them. The
    rest of the fields still make the destination useful in the meantime: a name,
    a dispatch note and access instructions get a responder to the right door
    even without a pin.

    `is_synthetic` is true only for a demo/development fixture (Phase 5B) —
    never for a real, surveyed location. It travels with the destination
    specifically so a responder-facing UI can mark a demo destination as
    what it is and never present it as an actual surveyed campus point.
    """

    location_id: int
    code: str
    name: str
    latitude: float | None
    longitude: float | None
    is_mapped: bool
    location_type: str | None
    is_indoor: bool | None
    dispatch_note: str | None
    zone_name: str | None
    location_hint: str | None
    is_synthetic: bool

    @property
    def navigable(self) -> bool:
        """Whether an external map can be handed a point.

        False for every location today. `CAMPUS_LOCATIONS.md` records zero
        verified coordinates, and this returns the honest answer rather than a
        campus centroid that would send a responder to the wrong building.
        """
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True, slots=True)
class IncidentDetail:
    """One incident, as a responder is entitled to see it."""

    row: IncidentRow
    destination: Destination
    narrative: str | None
    narrative_available: bool
    narrative_withheld_reason: str | None
    evidence_ids: list[str]
    triage: TriageView | None
    # Case-lifecycle data, bundled here rather than behind a second endpoint.
    # `IncidentService.detail` has already run authorisation once; `CaseService`
    # trusts that rather than repeating it, exactly as it trusts a pre-fetched
    # `Report` everywhere else.
    status_history: list[CaseStatusHistory] = field(default_factory=list)
    assignment: AssignmentView | None = None


class IncidentService:
    def __init__(
        self,
        *,
        incidents: IncidentRepository,
        reports: ReportRepository,
        triage: TriageService | None = None,
        cases: CaseService | None = None,
    ) -> None:
        self._incidents = incidents
        self._reports = reports
        self._triage = triage
        self._cases = cases

    # -- authorisation -----------------------------------------------------

    @staticmethod
    def can_use_responder_view(principal: Principal | None) -> bool:
        """Who may open the responder map at all.

        Students never, regardless of what the frontend shows. A student's own
        report is reachable through `GET /reports/<ref>`, which is a different
        thing: their report, not a queue of other people's.
        """
        return (
            principal is not None and principal.is_active and principal.role is not UserRole.STUDENT
        )

    def _require_responder(self, principal: Principal | None) -> Principal:
        if not self.can_use_responder_view(principal):
            raise AuthorizationError("This account cannot view the responder map.")
        assert principal is not None
        return principal

    # -- queue -------------------------------------------------------------

    def queue(
        self,
        principal: Principal | None,
        *,
        limit: int = 100,
        offset: int = 0,
        statuses: tuple[ReportStatus, ...] | None = None,
    ) -> tuple[list[IncidentRow], int]:
        responder = self._require_responder(principal)
        rows = self._incidents.active_incidents(
            responder.role, limit=limit, offset=offset, statuses=statuses
        )
        return rows, self._incidents.count_active(responder.role, statuses=statuses)

    # -- one incident ------------------------------------------------------

    def detail(self, principal: Principal | None, public_ref: str) -> IncidentDetail:
        responder = self._require_responder(principal)

        report = self._incidents.get_by_public_ref(public_ref)
        if report is None:
            raise NotFoundError("No such incident.")

        context = self._reports.access_context(report)
        if not can_view_report(responder, context):
            # 404 rather than 403. A 403 would confirm the reference is real to
            # someone who is not entitled to know that.
            raise NotFoundError("No such incident.")

        row = self._incidents.row_for_report(report)

        narrative_text: str | None = None
        withheld: str | None = None
        may_read = can_view_narrative(responder, context)
        if may_read:
            narrative = self._reports.get_narrative(report.report_id)
            narrative_text = narrative.narrative if narrative else None
        else:
            withheld = (
                "This category is handled confidentially by the ICC."
                if context.requires_confidentiality
                else "Your role has access to incident details but not to the reporter's account."
            )

        return IncidentDetail(
            row=row,
            destination=self.destination_for(row.location, report.location_hint),
            narrative=narrative_text,
            narrative_available=may_read,
            narrative_withheld_reason=withheld,
            evidence_ids=[
                str(evidence.evidence_id)
                for evidence in self._incidents.list_evidence(report.report_id)
            ],
            triage=(
                self._triage.view_for(report, visible=self._visible_report_ids(responder))
                if self._triage is not None
                else None
            ),
            status_history=(self._cases.status_history(report) if self._cases is not None else []),
            assignment=(
                self._cases.current_assignment(report) if self._cases is not None else None
            ),
        )

    def _visible_report_ids(self, responder: Principal) -> set[uuid.UUID]:
        """Reports this responder could already open on their own.

        Used to filter proposed links. **A link must never widen access**: it is
        metadata about a pair of reports, not a grant over either one. A
        responder who cannot see report B learns nothing about it by opening
        report A — not its reference, not its location, not that it exists.

        This matters most where an anonymous report is linked to an identified
        one. The link is legitimate — two people can report the same incident —
        but it must not become a path to an identity, and the surest way to
        ensure that is to show it only to someone already entitled to both sides.
        """
        return {
            row.report.report_id
            for row in self._incidents.active_incidents(responder.role, limit=500)
        }

    # -- destination -------------------------------------------------------

    @staticmethod
    def destination_for(location: CampusLocation, location_hint: str | None = None) -> Destination:
        """Turn the selected campus location into somewhere a responder can go.

        A coordinate is offered only when it is `verified`. `provisional` is
        treated as unmapped on purpose: a provisional coordinate is one nobody has
        stood at, and routing a responder to it during an emergency is worse than
        telling them plainly that the point is not surveyed yet.

        `dispatch_note` and `location_hint` are what make the last hundred metres
        work. A pin on a building is not the same as knowing to use the service
        entrance and take the lift to level two.
        """
        mapped = (
            location.coordinate_status is CoordinateStatus.VERIFIED
            and location.latitude is not None
            and location.longitude is not None
        )
        return Destination(
            location_id=location.location_id,
            code=location.code,
            name=location.name,
            # `is not None`, not truthiness: a coordinate of exactly 0.0 (a
            # legitimate point on the equator, and the value this project's
            # own synthetic test fixtures use) is falsy in Python, and the
            # previous `and location.latitude` check silently turned a
            # genuinely verified location into an unmapped one.
            latitude=float(location.latitude) if mapped and location.latitude is not None else None,
            longitude=(
                float(location.longitude) if mapped and location.longitude is not None else None
            ),
            is_mapped=mapped,
            location_type=location.location_type,
            is_indoor=location.is_indoor,
            dispatch_note=location.dispatch_note,
            zone_name=location.zone.name if location.zone else None,
            location_hint=location_hint,
            is_synthetic=location.is_synthetic,
        )

    # -- dispatch ----------------------------------------------------------

    def raise_dispatch(
        self, principal: Principal | None, public_ref: str, *, now: datetime | None = None
    ) -> EmergencyDispatch:
        """Open a dispatch against an incident.

        Always a human action. Nothing in this system dispatches automatically —
        not on an emergency flag, not on a category, not on a risk score. A
        responder decides to go.

        Only emergency reports can carry a dispatch. That is a database rule
        (`trg_dispatch_requires_emergency`), and it is checked here first so the
        caller gets a stated reason rather than a constraint violation — the same
        pattern `ReportRepository.attribute` uses for the anonymity trigger.
        """
        responder, report = self._authorised_report(principal, public_ref)
        now = now or datetime.now(timezone.utc)

        if not report.is_emergency:
            raise ConflictError(
                "Only an emergency report can be dispatched.",
                details={"public_ref": report.public_ref, "is_emergency": False},
            )

        existing = self._incidents.current_dispatch(report.report_id)
        if existing is not None and not existing.state.is_terminal:
            raise ConflictError(
                "A dispatch is already open for this incident.",
                details={"state": existing.state.value},
            )

        dispatch = self._incidents.add_dispatch(report.report_id, raised_at=now)
        logger.info("dispatch raised for %s by role=%s", report.public_ref, responder.role.value)
        return dispatch

    def advance_dispatch(
        self,
        principal: Principal | None,
        public_ref: str,
        target: DispatchState,
        *,
        note: str | None = None,
        now: datetime | None = None,
    ) -> EmergencyDispatch:
        """Move a dispatch to its next state, stamping the matching timestamp.

        The timestamp columns are not set by the caller. Each state owns exactly
        one column, and writing it here means the record of when a responder
        arrived cannot disagree with the state that says they did.
        """
        responder, report = self._authorised_report(principal, public_ref)
        now = now or datetime.now(timezone.utc)

        dispatch = self._incidents.current_dispatch(report.report_id)
        if dispatch is None:
            raise NotFoundError("No dispatch has been raised for this incident.")

        allowed = DISPATCH_TRANSITIONS[dispatch.state]
        if target not in allowed:
            raise ConflictError(
                f"A dispatch that is {dispatch.state.value} cannot become {target.value}.",
                details={
                    "state": dispatch.state.value,
                    "allowed": sorted(state.value for state in allowed),
                },
            )

        dispatch.state = target
        if target is DispatchState.ACKNOWLEDGED:
            dispatch.acknowledged_at = now
            # The only place a responder identity is recorded. This is who
            # answered, never who reported.
            dispatch.acknowledged_by = responder.user_id
        elif target is DispatchState.DISPATCHED:
            dispatch.dispatched_at = now
        elif target is DispatchState.ON_SCENE:
            dispatch.on_scene_at = now
        elif target in (DispatchState.CLOSED, DispatchState.STOOD_DOWN):
            dispatch.closed_at = now

        if note:
            # `responder_note` is internal. `public_note` is the field that would
            # ever reach a reporter, and nothing here writes it — releasing
            # information back to a reporter is a separate decision with its own
            # rules, not a side effect of arriving somewhere.
            dispatch.responder_note = note

        self._incidents.save(dispatch)
        logger.info(
            "dispatch for %s advanced to %s by role=%s",
            report.public_ref,
            target.value,
            responder.role.value,
        )
        return dispatch

    # -- machine assistance, corrected by a human --------------------------

    def override_category(
        self, principal: Principal | None, public_ref: str, category_id: int
    ) -> None:
        """Record that a responder judged the category differently.

        **This does not change the report.** `core.report.declared_category_id`
        is what the student chose and stays theirs; the responder's judgement is
        stored on the classification row, attributed and timestamped. Changing
        the report's own category is a case-management action with its own
        history trail, not a side effect of disagreeing with a model.
        """
        if self._triage is None:
            raise NotFoundError("No suggestion to correct.")
        responder, report = self._authorised_report(principal, public_ref)
        self._triage.override_category(report, category_id, responder.user_id)

    def review_link(
        self, principal: Principal | None, public_ref: str, link_id: int, confirmed: bool
    ) -> None:
        """Confirm or reject a proposed link.

        Only for a responder who can see **both** sides. Confirming a link to a
        report you cannot open would let you assert a relationship between your
        case and one you are not entitled to know exists.
        """
        if self._triage is None:
            raise NotFoundError("No link to review.")
        responder, report = self._authorised_report(principal, public_ref)
        visible = self._visible_report_ids(responder)
        self._triage.review_link(report, link_id, confirmed, responder.user_id, visible=visible)

    # -- shared ------------------------------------------------------------

    def _authorised_report(self, principal: Principal | None, public_ref: str):
        responder = self._require_responder(principal)
        report = self._incidents.get_by_public_ref(public_ref)
        if report is None:
            raise NotFoundError("No such incident.")
        context = self._reports.access_context(report)
        if not can_view_report(responder, context):
            raise NotFoundError("No such incident.")
        return responder, report

    @staticmethod
    def parse_dispatch_state(value: str) -> DispatchState:
        try:
            return DispatchState(value)
        except ValueError:
            raise ValidationError(
                "Unknown dispatch state.",
                details={
                    "fields": {
                        "state": [
                            "Expected one of: " + ", ".join(state.value for state in DispatchState)
                        ]
                    }
                },
            ) from None
