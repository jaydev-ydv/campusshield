"""Data access for case status transitions and assignment.

Two rules this module keeps, both mechanical consequences of `0001`'s design
rather than choices made here.

**Status is never written directly.** `record_transition` inserts into
`core.case_status_history`. `core.report.current_status` and `closed_at` are
updated by `trg_sync_report_status`, a database trigger, not by this
repository. There is no method here that writes `Report.current_status` —
deliberately: a path that could would make "when did this change and why"
answerable only sometimes.

**At most one active assignment.** `uq_case_assignment_active` is a partial
unique index on `report_id WHERE is_active`, so two active rows for one report
cannot exist regardless of what the application does. `reassign` still releases
the old row before creating the new one, in the same transaction, because
relying on the constraint to catch a bug would surface as a request failing
rather than as a queue that makes sense.

No query here can resolve a reporter. `identity.app_user` is read only to
validate and label a *responder* — the assignee, or whoever changed a status —
never to look up who filed the report.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppUser, CaseAssignment, CaseStatusHistory
from ..models.enums import ReportStatus, ResolutionReason, UserRole


@dataclass(frozen=True, slots=True)
class ResponderRef:
    """A staff member, named safely.

    ``display_name`` rather than email or ``user_id`` — the minimum needed for
    one teammate to recognise another on a shared case, and no more. Falls back
    to a role label when a display name was never set, which the schema permits
    even for authority accounts.
    """

    user_id: uuid.UUID
    role: UserRole
    display_name: str | None

    @property
    def label(self) -> str:
        if self.display_name:
            return self.display_name
        return {
            UserRole.ICC: "ICC officer",
            UserRole.SECURITY: "Security officer",
            UserRole.ADMIN: "Administrator",
        }.get(self.role, self.role.value)


class CaseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- status --------------------------------------------------------

    def record_transition(
        self,
        report_id: uuid.UUID,
        *,
        from_status: ReportStatus,
        to_status: ReportStatus,
        changed_by: uuid.UUID,
        remark: str | None,
        visible_to_reporter: bool,
        resolution_reason: ResolutionReason | None,
        now: datetime | None = None,
    ) -> CaseStatusHistory:
        row = CaseStatusHistory(
            report_id=report_id,
            from_status=from_status,
            to_status=to_status,
            changed_by=changed_by,
            changed_at=now or datetime.now(timezone.utc),
            remark=remark,
            visible_to_reporter=visible_to_reporter,
            resolution_reason=resolution_reason,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def full_history(self, report_id: uuid.UUID) -> list[CaseStatusHistory]:
        """Every transition, unfiltered.

        The responder view of a case. `visible_to_reporter` still governs what
        `ReportRepository.visible_status_history` returns to the reporter — this
        method is for the other side of that boundary and must never be reached
        from a reporter-facing route.
        """
        return list(
            self._session.scalars(
                select(CaseStatusHistory)
                .where(CaseStatusHistory.report_id == report_id)
                .order_by(CaseStatusHistory.changed_at)
            )
        )

    # -- assignment ------------------------------------------------------

    def active_assignment(self, report_id: uuid.UUID) -> CaseAssignment | None:
        return self._session.scalar(
            select(CaseAssignment).where(
                CaseAssignment.report_id == report_id, CaseAssignment.is_active.is_(True)
            )
        )

    def assignment_history(self, report_id: uuid.UUID) -> list[CaseAssignment]:
        return list(
            self._session.scalars(
                select(CaseAssignment)
                .where(CaseAssignment.report_id == report_id)
                .order_by(CaseAssignment.assigned_at)
            )
        )

    def add_assignment(
        self,
        report_id: uuid.UUID,
        *,
        assigned_to: uuid.UUID,
        assigned_role: UserRole,
        assigned_by: uuid.UUID | None,
        note: str | None,
        now: datetime | None = None,
    ) -> CaseAssignment:
        row = CaseAssignment(
            report_id=report_id,
            assigned_to=assigned_to,
            assigned_role=assigned_role,
            assigned_by=assigned_by,
            assigned_at=now or datetime.now(timezone.utc),
            assignment_note=note,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def release_assignment(
        self, assignment: CaseAssignment, *, now: datetime | None = None
    ) -> None:
        assignment.is_active = False
        assignment.released_at = now or datetime.now(timezone.utc)
        self._session.flush()

    # -- responder lookup --------------------------------------------------

    def get_responder(self, user_id: uuid.UUID) -> ResponderRef | None:
        """A staff member, for validating and labelling an assignment target.

        Returns ``None`` for a student as well as for a nonexistent id: the
        database trigger would refuse a student assignment anyway, but this lets
        the service reject it before it becomes a constraint violation, matching
        the pattern `ReportRepository.attribute` uses for the anonymity trigger.
        """
        user = self._session.get(AppUser, user_id)
        if user is None or user.role is UserRole.STUDENT or not user.is_active:
            return None
        return ResponderRef(user_id=user.user_id, role=user.role, display_name=user.display_name)
