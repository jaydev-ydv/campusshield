"""Data access for the responder view: the incident queue, dispatch, location detail.

An "incident" is not a new entity. It is `core.report` read from a responder's
point of view — with the location joined in, the dispatch state attached, and the
narrative deliberately left behind for a separate authorised call.

**No query in this module touches `identity.report_attribution`.** The responder
plane never needs to know who filed something, so it is not given the ability to
ask. That is a stronger guarantee than remembering not to select the column:
there is no join here to forget to remove.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..models import (
    CampusLocation,
    EmergencyDispatch,
    EvidenceObject,
    Report,
    ReportCategory,
    ReportLocationDetail,
)
from ..models.enums import DispatchState, ReportStatus, UserRole

# Statuses that have left a responder's queue. A resolved or withdrawn report is
# history, not work.
CLOSED_STATUSES = (
    ReportStatus.RESOLVED,
    ReportStatus.CLOSED_NO_ACTION,
    ReportStatus.WITHDRAWN,
    ReportStatus.DUPLICATE,
)


@dataclass(frozen=True, slots=True)
class IncidentRow:
    """One row of the responder queue.

    Assembled explicitly rather than returned as ORM objects, so what reaches a
    serialiser is a fixed set of fields chosen here. Note what is not on it:
    no reporter, no narrative, no storage path.
    """

    report: Report
    location: CampusLocation
    category: ReportCategory | None
    dispatch: EmergencyDispatch | None
    location_detail: ReportLocationDetail | None
    evidence_count: int
    # Whether the case currently has an owner. A bare boolean rather than the
    # assignee's identity: the queue is a scanning view, and who exactly holds a
    # case is the detail view's job (`CaseService.current_assignment`, surfaced
    # through `serialize_assignment`). Read the same way
    # `ReportRepository._active_assignee` already does, rather than importing
    # `CaseRepository` here — repositories in this codebase stay scoped to their
    # own tables; composing across them is a service's job, and a bare EXISTS
    # check is cheap enough not to need that composition for this one field.
    is_assigned: bool


class IncidentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- queue -------------------------------------------------------------

    def _visible_to(
        self, role: UserRole, *, statuses: tuple[ReportStatus, ...] | None = None
    ) -> Select[tuple[Report]]:
        """Reports whose category routes to this role.

        The same rule `can_view_report` applies to a single report, expressed as
        a query so the queue cannot show something the detail endpoint would then
        refuse. A role is not access: an ICC member sees reports routed to the
        ICC, not every report.

        `admin` is deliberately absent from the routing check — administration
        can see report metadata for oversight, and `can_view_report` already says
        so — but it still gets no narrative and no precise coordinate.

        `statuses`, when given, narrows within the open set rather than
        replacing it — a responder filtering to "under review" must still never
        see a report closed to a different role. Requesting a closed status
        explicitly returns nothing rather than silently reaching past
        `CLOSED_STATUSES`: this method is "what's in my open queue," not a
        general report search.
        """
        stmt = (
            select(Report)
            .join(ReportCategory, ReportCategory.category_id == Report.declared_category_id)
            .where(Report.current_status.not_in(CLOSED_STATUSES))
        )
        if role is not UserRole.ADMIN:
            stmt = stmt.where(ReportCategory.routes_to_role == role)
        if statuses:
            stmt = stmt.where(Report.current_status.in_(statuses))
        return stmt

    def active_incidents(
        self,
        role: UserRole,
        *,
        limit: int = 100,
        offset: int = 0,
        statuses: tuple[ReportStatus, ...] | None = None,
    ) -> list[IncidentRow]:
        """The open queue for a responder role.

        Ordered by emergency, then by ongoing, then most recent first.

        That ordering comes entirely from the incident: whether it was raised as
        an emergency, whether it is still happening, and when it arrived. It is
        deliberately blind to `reporter_relationship` — which records vantage
        point, not credibility — and to anything about who reported it, including
        how many reports they have filed before. A queue that sorted on the
        reporter would be a credibility score with no name.
        """
        stmt = (
            self._visible_to(role, statuses=statuses)
            .order_by(
                Report.is_emergency.desc(),
                Report.is_ongoing.desc(),
                Report.submitted_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return [self._row_for(report) for report in self._session.scalars(stmt).unique()]

    def count_active(
        self, role: UserRole, *, statuses: tuple[ReportStatus, ...] | None = None
    ) -> int:
        inner = self._visible_to(role, statuses=statuses).subquery()
        return self._session.scalar(select(func.count()).select_from(inner)) or 0

    # -- one incident ------------------------------------------------------

    def get_by_public_ref(self, public_ref: str) -> Report | None:
        return self._session.scalar(select(Report).where(Report.public_ref == public_ref))

    def row_for_report(self, report: Report) -> IncidentRow:
        return self._row_for(report)

    def _row_for(self, report: Report) -> IncidentRow:
        location = self._session.get(CampusLocation, report.location_id)
        assert location is not None  # NOT NULL FK; absence would be corruption
        category = (
            self._session.get(ReportCategory, report.declared_category_id)
            if report.declared_category_id is not None
            else None
        )
        return IncidentRow(
            report=report,
            location=location,
            category=category,
            dispatch=self.current_dispatch(report.report_id),
            location_detail=self._session.get(ReportLocationDetail, report.report_id),
            evidence_count=self.evidence_count(report.report_id),
            is_assigned=self._is_assigned(report.report_id),
        )

    def _is_assigned(self, report_id: uuid.UUID) -> bool:
        return bool(
            self._session.scalar(
                sql_text(
                    "SELECT 1 FROM core.case_assignment "
                    "WHERE report_id = :rid AND is_active LIMIT 1"
                ),
                {"rid": str(report_id)},
            )
        )

    def evidence_count(self, report_id: uuid.UUID) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(EvidenceObject)
                .where(
                    EvidenceObject.report_id == report_id,
                    EvidenceObject.is_purged.is_(False),
                )
            )
            or 0
        )

    def list_evidence(self, report_id: uuid.UUID) -> list[EvidenceObject]:
        return list(
            self._session.scalars(
                select(EvidenceObject)
                .where(
                    EvidenceObject.report_id == report_id,
                    EvidenceObject.is_purged.is_(False),
                )
                .order_by(EvidenceObject.uploaded_at)
            )
        )

    # -- dispatch ----------------------------------------------------------

    def current_dispatch(self, report_id: uuid.UUID) -> EmergencyDispatch | None:
        """The most recent dispatch for a report.

        A report can be dispatched more than once — stood down, then raised
        again when something changes. The latest is the operative one.
        """
        return self._session.scalar(
            select(EmergencyDispatch)
            .where(EmergencyDispatch.report_id == report_id)
            .order_by(EmergencyDispatch.raised_at.desc())
            .limit(1)
        )

    def get_dispatch(self, dispatch_id: uuid.UUID) -> EmergencyDispatch | None:
        return self._session.get(EmergencyDispatch, dispatch_id)

    def add_dispatch(self, report_id: uuid.UUID, *, raised_at: datetime) -> EmergencyDispatch:
        dispatch = EmergencyDispatch(
            report_id=report_id,
            state=DispatchState.PENDING,
            raised_at=raised_at,
        )
        self._session.add(dispatch)
        self._session.flush()
        return dispatch

    def save(self, dispatch: EmergencyDispatch) -> EmergencyDispatch:
        self._session.add(dispatch)
        self._session.flush()
        return dispatch

    # -- location detail ---------------------------------------------------

    def get_location_detail(self, report_id: uuid.UUID) -> ReportLocationDetail | None:
        return self._session.get(ReportLocationDetail, report_id)
