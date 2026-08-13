"""In-app notifications: status updates and assignment, never content.

## What this delivers, honestly

A row in `notify.notification`, immediately readable through `GET
/notifications`. That is the whole delivery mechanism this phase builds.
There is no push, no email, and no background worker — `Notification.
fcm_message_id` is never set, `notify.device_token` is never written, and
nothing here pretends otherwise. `DeliveryState.SENT` is stamped at creation
because the notification genuinely is available the moment the row exists;
it is not a claim about a device receiving anything.

## Two triggers, both already named by `notification_category` in `0001`

`status_update` — a case moved to a status the reporter is allowed to see
(`visible_to_reporter=True` on the `core.case_status_history` row).
`assignment` — a responder was handed a case by someone other than themselves;
self-assignment needs no notification, since the person doing it already
knows.

## Who can never be notified

An anonymous report has no row in `identity.report_attribution`, so
`notify_status_change` has no `recipient_user_id` to write and is a deliberate
no-op for one. This is not a gap to close — see `notify.notification`'s own
design note: a device-token-to-report binding would rebuild the exact link
anonymity exists to prevent, and was rejected when this table was designed.

## What never appears in a title or body

Narrative text, evidence content, risk scores, ML output, or any other
report detail. Every template here is a fixed string plus a status label
and a public reference — nothing sourced from what a student or a responder
wrote.
"""

from __future__ import annotations

import logging
import uuid

from ..models import Report
from ..models.enums import NotificationCategory, ReportStatus, SubmissionMode
from ..repositories.notification_repository import NotificationRepository
from ..repositories.report_repository import ReportRepository

logger = logging.getLogger(__name__)

# Mirrors the frontend's STATUS_LABELS exactly. Kept here, independently,
# because a notification must remain meaningful even if the frontend's copy
# changes — the two are allowed to drift in wording, never in which statuses
# exist, and a test asserts this dict's keys equal the full ReportStatus enum.
_STATUS_LABELS: dict[ReportStatus, str] = {
    ReportStatus.SUBMITTED: "received",
    ReportStatus.TRIAGED: "triaged",
    ReportStatus.UNDER_REVIEW: "under review",
    ReportStatus.ACTION_TAKEN: "had action taken",
    ReportStatus.RESOLVED: "resolved",
    ReportStatus.CLOSED_NO_ACTION: "closed",
    ReportStatus.DUPLICATE: "linked to another report",
    ReportStatus.WITHDRAWN: "withdrawn",
}


class NotificationService:
    def __init__(self, *, notifications: NotificationRepository, reports: ReportRepository) -> None:
        self._notifications = notifications
        self._reports = reports

    def notify_status_change(self, report: Report, *, to_status: ReportStatus) -> None:
        """Tell the reporter their case moved, and nothing about how.

        Silently does nothing for an anonymous report (no recipient exists)
        and nothing for a status the reporter was not shown (the caller is
        responsible for only calling this when `visible_to_reporter` was
        true — `CaseService` already knows that at the point it calls this).
        """
        if report.submission_mode is not SubmissionMode.IDENTIFIED:
            return

        recipient = self._reporter_user_id(report)
        if recipient is None:
            return  # pragma: no cover - identified reports always have one

        label = _STATUS_LABELS[to_status]
        self._notifications.create_for_user(
            recipient,
            category=NotificationCategory.STATUS_UPDATE,
            title="Your report status changed",
            body=f"Report {report.public_ref} is now {label}.",
            related_report_id=report.report_id,
        )

    def notify_assignment(self, report: Report, *, assignee_user_id: uuid.UUID) -> None:
        """Tell a responder they now own a case — never the reporter."""
        self._notifications.create_for_user(
            assignee_user_id,
            category=NotificationCategory.ASSIGNMENT,
            title="A case was assigned to you",
            body=f"You have been assigned {report.public_ref}.",
            related_report_id=report.report_id,
        )

    def _reporter_user_id(self, report: Report) -> uuid.UUID | None:
        context = self._reports.access_context(report)
        return context.reporter_user_id
