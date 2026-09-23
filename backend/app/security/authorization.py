"""Authorisation policy — who may see what.

Kept in one module, as pure functions over explicit inputs, so the answer to
"can this person read this report?" is decided in exactly one place and can be
read end to end. Routes ask; they do not decide.

Two rules shape everything here.

**Being an authority is not access.**  A role alone never grants sight of a
report.  An ICC member sees reports routed to the ICC; a security officer sees
reports routed to security.  Anything else is "give everyone all reports" wearing
a role check.

**Metadata and narrative are separate decisions.**  ``admin`` can see report
metadata for analytics and can see none of the narratives, mirroring the database
grants where ``cs_analytics`` is denied ``SELECT`` on ``core.report_narrative``
outright.  Administration gets the pattern layer without reading a student's
account of what happened to them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..models.enums import UserRole
from .principal import Principal


@dataclass(frozen=True, slots=True)
class ReportAccessContext:
    """The minimum facts an access decision needs.

    Assembled by the repository so the policy never issues its own queries and
    can be unit-tested without a database.  ``reporter_user_id`` is ``None`` for
    every anonymous report, because no attribution row exists — the policy cannot
    accidentally leak an identity it is never given.
    """

    report_id: uuid.UUID
    routes_to_role: UserRole | None
    requires_confidentiality: bool
    reporter_user_id: uuid.UUID | None
    assigned_to_user_id: uuid.UUID | None
    accessed_via_token: bool = False


def is_reporter(principal: Principal | None, ctx: ReportAccessContext) -> bool:
    if principal is None or ctx.reporter_user_id is None:
        return False
    return principal.user_id == ctx.reporter_user_id


def can_create_report(principal: Principal | None) -> bool:
    """Any authenticated, active account may file.

    Not students-only: a warden or a security officer who witnesses something
    should be able to report it through the same channel, and ``reporter_relationship``
    already records the vantage point.  Authentication is still required — it is
    what makes ``identity.submission_quota`` able to rate-limit anonymous
    submissions without linking them to anybody.
    """
    return principal is not None and principal.is_active


def can_view_report(principal: Principal | None, ctx: ReportAccessContext) -> bool:
    """May the caller see this report's metadata and status?"""
    if ctx.accessed_via_token:
        # An anonymous reporter holding the one-time token issued at submission.
        # Possession of the token is the authorisation; there is no identity to
        # check against, which is the point.
        return True
    if principal is None:
        return False
    if is_reporter(principal, ctx):
        return True
    if principal.role is UserRole.STUDENT:
        # A student is never anything but the reporter of their own report.
        return False
    if ctx.assigned_to_user_id is not None and principal.user_id == ctx.assigned_to_user_id:
        return True
    if principal.role is UserRole.ADMIN:
        # Metadata only — see can_view_narrative.
        return True
    return ctx.routes_to_role is not None and principal.role is ctx.routes_to_role


def can_view_narrative(principal: Principal | None, ctx: ReportAccessContext) -> bool:
    """May the caller read the account itself?

    Strictly narrower than :func:`can_view_report`.  Administration is excluded
    even though it can see the report, and confidential categories are restricted
    to the ICC and the assigned officer regardless of role.
    """
    if ctx.accessed_via_token:
        return True
    if principal is None:
        return False
    if is_reporter(principal, ctx):
        return True
    if principal.role is UserRole.STUDENT:
        return False
    if principal.role is UserRole.ADMIN:
        # The narrative firewall. Administration analyses patterns, not accounts.
        return False
    if ctx.assigned_to_user_id is not None and principal.user_id == ctx.assigned_to_user_id:
        return True
    if ctx.requires_confidentiality:
        return principal.role is UserRole.ICC
    return ctx.routes_to_role is not None and principal.role is ctx.routes_to_role


def can_manage_case(principal: Principal | None, ctx: ReportAccessContext) -> bool:
    """May the caller change this case's status, or its assignment?

    Deliberately narrower than :func:`can_view_report`.

    **Token access is excluded outright.** An anonymous reporter holding their
    own access token can read their status; they can never move it. The token
    proves "this is my report," not "I am staff."

    **Administration is excluded**, for the same reason it is excluded from
    :func:`can_view_narrative`: it analyses patterns, not accounts, and moving a
    case forward is an operational act on one account, not oversight of many.

    A student is never included — the ``STUDENT`` branch is implicit here,
    because the two remaining conditions (assignee, routed role) can never be
    true for a student: assignment is trigger-refused for students, and no
    category ever routes to ``student``.
    """
    if principal is None:
        return False
    if principal.role is UserRole.ADMIN:
        return False
    if ctx.assigned_to_user_id is not None and principal.user_id == ctx.assigned_to_user_id:
        return True
    return ctx.routes_to_role is not None and principal.role is ctx.routes_to_role


def can_attach_evidence(principal: Principal | None, ctx: ReportAccessContext) -> bool:
    """May the caller add evidence to this already-submitted report?

    Reporter only, and — unlike :func:`can_view_report` — **not** via an
    anonymous access token. A token proves "let me read my own status," not
    "let me add binding evidence"; extending it to writes was never something
    the token's design considered. In practice this also confines
    evidence-after-creation to the emergency path, since that path is always
    identified (see ``ReportService.submit_sos``) — a real gap for anonymous
    reporters, tracked as future scope rather than solved by stretching the
    token's meaning under this feature's deadline.
    """
    return is_reporter(principal, ctx)


def can_resolve_identity(principal: Principal | None, ctx: ReportAccessContext) -> bool:
    """May the caller learn *who* filed this?

    Only the ICC, only for an identified report, and only for one routed to them.
    Every such resolution must also write to ``audit.identity_disclosure_log``
    with a stated purpose — this function answers permission, not procedure.

    Phase 1 exposes no endpoint that calls this.  It is defined now so that when
    one is built, the rule already exists rather than being invented under
    deadline.
    """
    if principal is None or ctx.reporter_user_id is None:
        return False
    if principal.role is not UserRole.ICC:
        return False
    return ctx.routes_to_role is UserRole.ICC
