"""The case lifecycle: status transitions and assignment.

This is deliberately a separate service from `IncidentService`, even though both
act on `core.report` through the responder plane. The reason is the same one
stated to the frontend: **case status and dispatch status are related but not
the same state machine.** Dispatch (`pending → … → closed`) tracks one physical
response to one emergency and is owned by `IncidentService`, which has done so
since 4B-2. Case status (`submitted → … → resolved`) tracks the institutional
handling of the report itself — triage, investigation, outcome — and is owned
here. A case can be `resolved` with no dispatch ever raised, and a dispatch can
be `closed` while the case is still `under_review`; neither implies the other,
and nothing in this module reads or writes `core.emergency_dispatch`.

## The state machine

Values are exactly `public.report_status` from `0001` — no parallel vocabulary.
`CASE_TRANSITIONS` is the only place the legal graph is written down; a route
cannot express an illegal move because every move goes through
:meth:`CaseService.change_status`.

```
submitted ──▶ triaged ──▶ under_review ──┬──▶ action_taken ──▶ resolved
    │             │             │        └──────────────────────▲
    ├─▶ withdrawn ┤             ├─▶ resolved
    ├─▶ duplicate ┤             ├─▶ closed_no_action
    └─▶ closed_no_action        ├─▶ withdrawn
                                 └─▶ duplicate
```

`resolved`, `closed_no_action`, `duplicate`, `withdrawn` are terminal: nothing
in `CASE_TRANSITIONS` lists a successor for them, and
:meth:`CaseService.change_status` refuses to move a report out of one. The only
way past that refusal is a direct database operation by someone with production
access — deliberately not a feature, because a "reopen" action would need its own
authorisation story this phase does not have a mandate to design.

## What requires an assignment, and why

`triaged → under_review`, and everything that follows it, requires an active
`core.case_assignment` row *at the moment of the transition*. Investigating and
closing a case are accountable acts; the schema's own unique index already
insists on at most one owner, and this rule insists there be at least one before
real work is recorded against the case. Acknowledging a fresh report
(`submitted → triaged`) does not — a first look does not yet need an owner —
and neither do the early exits (`closed_no_action`, `duplicate`, `withdrawn`
reachable directly from `submitted` or `triaged`): a case can be dismissed or
merged before anyone has taken it on.

Releasing the active assignment does not roll the case backward. A case that
becomes unowned mid-investigation stays exactly where it is and simply
reappears in the unassigned queue — the correct behaviour for a handover, not
an error condition.

## Resolution reasons

Required on every terminal transition, forbidden on every other one — enforced
twice, once here (so the caller gets a stated reason) and once by
`ck_case_status_resolution_reason_terminal` (so the rule holds even if this
service is bypassed). `RESOLUTION_REASONS_BY_STATUS` additionally restricts
*which* reasons make sense for *which* terminal status: `duplicate` cannot close
as `withdrawn_by_reporter`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..errors import ConflictError, NotFoundError, ValidationError
from ..models import CaseAssignment, CaseStatusHistory, Report
from ..models.enums import ReportStatus, ResolutionReason
from ..repositories.case_repository import CaseRepository, ResponderRef
from ..repositories.report_repository import ReportRepository
from ..security.authorization import can_manage_case
from ..security.principal import Principal
from .notification_service import NotificationService

TERMINAL_STATUSES: frozenset[ReportStatus] = frozenset(
    {
        ReportStatus.RESOLVED,
        ReportStatus.CLOSED_NO_ACTION,
        ReportStatus.DUPLICATE,
        ReportStatus.WITHDRAWN,
    }
)

# The legal graph. Mirrors DISPATCH_TRANSITIONS in incident_service.py in shape,
# not in vocabulary — the two state machines share a pattern, not a meaning.
CASE_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.SUBMITTED: frozenset(
        {
            ReportStatus.TRIAGED,
            ReportStatus.WITHDRAWN,
            ReportStatus.DUPLICATE,
            ReportStatus.CLOSED_NO_ACTION,
        }
    ),
    ReportStatus.TRIAGED: frozenset(
        {
            ReportStatus.UNDER_REVIEW,
            ReportStatus.WITHDRAWN,
            ReportStatus.DUPLICATE,
            ReportStatus.CLOSED_NO_ACTION,
        }
    ),
    ReportStatus.UNDER_REVIEW: frozenset(
        {
            ReportStatus.ACTION_TAKEN,
            ReportStatus.RESOLVED,
            ReportStatus.WITHDRAWN,
            ReportStatus.DUPLICATE,
            ReportStatus.CLOSED_NO_ACTION,
        }
    ),
    ReportStatus.ACTION_TAKEN: frozenset({ReportStatus.RESOLVED, ReportStatus.CLOSED_NO_ACTION}),
    ReportStatus.RESOLVED: frozenset(),
    ReportStatus.CLOSED_NO_ACTION: frozenset(),
    ReportStatus.DUPLICATE: frozenset(),
    ReportStatus.WITHDRAWN: frozenset(),
}

# Real investigative work needs an owner. A first look, or an early dismissal,
# does not.
REQUIRES_ACTIVE_ASSIGNMENT: frozenset[ReportStatus] = frozenset(
    {ReportStatus.UNDER_REVIEW, ReportStatus.ACTION_TAKEN, ReportStatus.RESOLVED}
)

# Every transition beyond the first acknowledgement must say why. Only the
# initial "someone looked at this" move is allowed to carry nothing.
REMARK_OPTIONAL: frozenset[ReportStatus] = frozenset({ReportStatus.TRIAGED})

RESOLUTION_REASONS_BY_STATUS: dict[ReportStatus, frozenset[ResolutionReason]] = {
    ReportStatus.RESOLVED: frozenset(
        {
            ResolutionReason.ACTION_TAKEN,
            ResolutionReason.REFERRED_ELSEWHERE,
            ResolutionReason.OTHER,
        }
    ),
    ReportStatus.CLOSED_NO_ACTION: frozenset(
        {
            ResolutionReason.NO_ACTION_WARRANTED,
            ResolutionReason.INSUFFICIENT_INFORMATION,
            ResolutionReason.REFERRED_ELSEWHERE,
            ResolutionReason.OTHER,
        }
    ),
    ReportStatus.DUPLICATE: frozenset(
        {ResolutionReason.DUPLICATE_OF_EXISTING_CASE, ResolutionReason.OTHER}
    ),
    ReportStatus.WITHDRAWN: frozenset(
        {ResolutionReason.WITHDRAWN_BY_REPORTER, ResolutionReason.OTHER}
    ),
}


@dataclass(frozen=True, slots=True)
class AssignmentView:
    assignment: CaseAssignment
    assignee: ResponderRef
    assigned_by: ResponderRef | None


class CaseService:
    def __init__(
        self,
        *,
        cases: CaseRepository,
        reports: ReportRepository,
        notifications: NotificationService | None = None,
    ) -> None:
        self._cases = cases
        self._reports = reports
        # Optional so the service is constructible without a notification
        # dependency — tests that only care about the state machine, and any
        # call site that has no need to notify, run without one. Matches the
        # optional-`triage` pattern already used in `ReportService`.
        self._notifications = notifications

    # -- status --------------------------------------------------------

    def change_status(
        self,
        principal: Principal | None,
        public_ref: str,
        target: ReportStatus,
        *,
        remark: str | None,
        resolution_reason: ResolutionReason | None,
        visible_to_reporter: bool = True,
        now: datetime | None = None,
    ) -> CaseStatusHistory:
        responder, report = self._authorised(principal, public_ref)
        now = now or datetime.now(timezone.utc)
        current = report.current_status

        allowed = CASE_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise ConflictError(
                f"A case that is {current.value} cannot become {target.value}.",
                details={"status": current.value, "allowed": sorted(s.value for s in allowed)},
            )

        if target not in REMARK_OPTIONAL and not remark:
            raise ValidationError(
                "This transition requires a stated reason.",
                details={"fields": {"remark": ["Required for this transition."]}},
            )

        is_terminal = target in TERMINAL_STATUSES
        if is_terminal:
            if resolution_reason is None:
                raise ValidationError(
                    "A terminal status requires a resolution reason.",
                    details={"fields": {"resolution_reason": ["Required for this transition."]}},
                )
            valid = RESOLUTION_REASONS_BY_STATUS.get(target, frozenset())
            if resolution_reason not in valid:
                raise ValidationError(
                    f"{resolution_reason.value} is not a valid reason to close as {target.value}.",
                    details={
                        "fields": {
                            "resolution_reason": [
                                "Expected one of: " + ", ".join(sorted(r.value for r in valid))
                            ]
                        }
                    },
                )
        elif resolution_reason is not None:
            raise ValidationError(
                "A resolution reason may only be given on a terminal transition.",
                details={"fields": {"resolution_reason": ["Not allowed for this transition."]}},
            )

        if (
            target in REQUIRES_ACTIVE_ASSIGNMENT
            and self._cases.active_assignment(report.report_id) is None
        ):
            raise ConflictError(
                f"{target.value} requires the case to be assigned first.",
                details={"status": current.value, "target": target.value},
            )

        entry = self._cases.record_transition(
            report.report_id,
            from_status=current,
            to_status=target,
            changed_by=responder.user_id,
            remark=remark,
            visible_to_reporter=visible_to_reporter,
            resolution_reason=resolution_reason,
            now=now,
        )
        # `report.current_status` is now stale in this Python object — the
        # trigger updated the row, not the mapped instance. Nothing below reads
        # `report.current_status` again, so this is safe, but it is exactly the
        # kind of staleness the docstring above warns about.

        if self._notifications is not None and visible_to_reporter:
            self._notifications.notify_status_change(report, to_status=target)

        return entry

    # -- assignment ------------------------------------------------------

    def assign(
        self,
        principal: Principal | None,
        public_ref: str,
        *,
        assignee_user_id: uuid.UUID | None,
        note: str | None,
        now: datetime | None = None,
    ) -> AssignmentView:
        """Claim a case, or hand it to a named colleague.

        ``assignee_user_id=None`` means self-assign — by far the common case in
        a shared queue, and the one action every authorised responder can always
        take without knowing anyone else's identifier. An explicit id assigns to
        that person instead, provided they hold the role this report is routed
        to; the database trigger would refuse a student regardless, but a
        mismatched *authority* role (a security officer on an ICC case) is a
        routing error this service catches before it becomes a confusing 500.
        """
        responder, report = self._authorised(principal, public_ref)
        now = now or datetime.now(timezone.utc)

        if report.current_status in TERMINAL_STATUSES:
            raise ConflictError(
                "This case is closed and cannot be reassigned.",
                details={"status": report.current_status.value},
            )

        target_id = assignee_user_id or responder.user_id
        # Looked up either way, self included — the display name lives on
        # `identity.app_user`, not on `Principal`, and a self-assignment should
        # show the same name a colleague assigning it to them would see.
        found = self._cases.get_responder(target_id)
        if found is None:
            if target_id == responder.user_id:
                # The authenticated caller was just verified active; a lookup
                # racing to None here is a deactivation mid-request, not a
                # reason to fail a self-assign with a confusing validation error.
                found = self._ref(responder)
            else:
                raise ValidationError(
                    "That account cannot be assigned a case.",
                    details={"fields": {"assignee_user_id": ["Unknown, inactive, or a student."]}},
                )
        target: ResponderRef = found

        context = self._reports.access_context(report)
        if context.routes_to_role is not None and target.role is not context.routes_to_role:
            raise ValidationError(
                f"This case is routed to {context.routes_to_role.value}; "
                f"{target.role.value} cannot be assigned.",
                details={"fields": {"assignee_user_id": ["Role does not match routing."]}},
            )

        existing = self._cases.active_assignment(report.report_id)
        if existing is not None:
            if existing.assigned_to == target.user_id:
                is_self = target.user_id == responder.user_id
                raise ConflictError(
                    "This case is already assigned to that person.",
                    details={"assignee": "self" if is_self else "other"},
                )
            self._cases.release_assignment(existing, now=now)

        row = self._cases.add_assignment(
            report.report_id,
            assigned_to=target.user_id,
            assigned_role=target.role,
            assigned_by=responder.user_id,
            note=note,
            now=now,
        )

        # Self-assignment needs no notification — the person doing it already
        # knows. Only a handoff to someone else is news to the recipient.
        if self._notifications is not None and target.user_id != responder.user_id:
            self._notifications.notify_assignment(report, assignee_user_id=target.user_id)

        return AssignmentView(assignment=row, assignee=target, assigned_by=self._ref(responder))

    def unassign(
        self,
        principal: Principal | None,
        public_ref: str,
        *,
        note: str | None,
        now: datetime | None = None,
    ) -> None:
        # `_authorised` is called for its authorisation side effect. Who
        # released the assignment is not a column on `case_assignment` — that
        # fact belongs to the audit log (the route writes it there), not to a
        # row this table was never designed to carry.
        _responder, report = self._authorised(principal, public_ref)

        existing = self._cases.active_assignment(report.report_id)
        if existing is None:
            raise NotFoundError("This case has no active assignment.")

        if note:
            existing.assignment_note = note
        self._cases.release_assignment(existing, now=now or datetime.now(timezone.utc))

    # -- reads (already-authorised report) --------------------------------

    def status_history(self, report: Report) -> list[CaseStatusHistory]:
        """Full history for an already-authorised responder view.

        Takes a `Report`, not a `public_ref` plus a principal, because this is
        called from `IncidentService.detail` after that method has already run
        its own authorisation. Re-checking here would be a second, divergent
        policy for the same question.
        """
        return self._cases.full_history(report.report_id)

    def current_assignment(self, report: Report) -> AssignmentView | None:
        row = self._cases.active_assignment(report.report_id)
        if row is None:
            return None
        assignee = self._cases.get_responder(row.assigned_to)
        if assignee is None:
            # The assignee's account was deactivated after the assignment was
            # made. The row still describes what happened; the label degrades
            # rather than the read failing.
            assignee = ResponderRef(
                user_id=row.assigned_to, role=row.assigned_role, display_name=None
            )
        assigned_by = self._cases.get_responder(row.assigned_by) if row.assigned_by else None
        return AssignmentView(assignment=row, assignee=assignee, assigned_by=assigned_by)

    # -- shared ------------------------------------------------------------

    def _authorised(self, principal: Principal | None, public_ref: str) -> tuple[Principal, Report]:
        report = self._reports.get_by_public_ref(public_ref)
        if report is None:
            raise NotFoundError("No such incident.")
        context = self._reports.access_context(report)
        if not can_manage_case(principal, context):
            # 404, matching every other responder-plane refusal in this
            # codebase: a 403 would confirm the reference is real to someone not
            # entitled to know that.
            raise NotFoundError("No such incident.")
        assert principal is not None
        return principal, report

    @staticmethod
    def _ref(principal: Principal) -> ResponderRef:
        return ResponderRef(user_id=principal.user_id, role=principal.role, display_name=None)

    @staticmethod
    def parse_status(value: str) -> ReportStatus:
        try:
            return ReportStatus(value)
        except ValueError:
            raise ValidationError(
                "Unknown case status.",
                details={
                    "fields": {
                        "status": ["Expected one of: " + ", ".join(s.value for s in ReportStatus)]
                    }
                },
            ) from None

    @staticmethod
    def parse_resolution_reason(value: str) -> ResolutionReason:
        try:
            return ResolutionReason(value)
        except ValueError:
            raise ValidationError(
                "Unknown resolution reason.",
                details={
                    "fields": {
                        "resolution_reason": [
                            "Expected one of: " + ", ".join(r.value for r in ResolutionReason)
                        ]
                    }
                },
            ) from None
