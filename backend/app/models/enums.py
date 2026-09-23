"""Python mirrors of the PostgreSQL enum types.

``create_type=False`` throughout: the types already exist, created by the
migration.  Letting SQLAlchemy try to create them would fail on every run after
the first.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class UserRole(str, enum.Enum):
    STUDENT = "student"
    SECURITY = "security"
    ICC = "icc"
    ADMIN = "admin"

    @property
    def is_authority(self) -> bool:
        return self is not UserRole.STUDENT


class ReportKind(str, enum.Enum):
    INCIDENT = "incident"
    CONCERN = "concern"


class SubmissionMode(str, enum.Enum):
    IDENTIFIED = "identified"
    ANONYMOUS = "anonymous"


class ReporterRelationship(str, enum.Enum):
    """Vantage point, never credibility.

    Prohibited from risk scoring, from any credibility or trust computation, from
    queue ordering, and from any UI treatment implying a report is less serious.
    ``core.risk_assessment`` enforces the first of those with a CHECK constraint.
    """

    AFFECTED = "affected"
    WITNESS = "witness"
    THIRD_PARTY = "third_party"


class ReportStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    TRIAGED = "triaged"
    UNDER_REVIEW = "under_review"
    ACTION_TAKEN = "action_taken"
    RESOLVED = "resolved"
    CLOSED_NO_ACTION = "closed_no_action"
    DUPLICATE = "duplicate"
    WITHDRAWN = "withdrawn"


class ResolutionReason(str, enum.Enum):
    """Why a case reached a terminal status.

    A controlled outcome vocabulary, not a judgement. ``NO_ACTION_WARRANTED``
    says a process concluded without formal action; it does not assert that
    nothing happened, only that this system's process did not result in one. A
    reporting tool that appeared to rule on the truth of an allegation would be
    overstepping in a way that damages trust in both directions. Enforced by
    ``ck_case_status_resolution_reason_terminal``: required on every terminal
    transition, forbidden on every other one.
    """

    ACTION_TAKEN = "action_taken"
    NO_ACTION_WARRANTED = "no_action_warranted"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    REFERRED_ELSEWHERE = "referred_elsewhere"
    DUPLICATE_OF_EXISTING_CASE = "duplicate_of_existing_case"
    WITHDRAWN_BY_REPORTER = "withdrawn_by_reporter"
    OTHER = "other"


class RiskBand(str, enum.Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class CoordinateStatus(str, enum.Enum):
    REQUIRED = "required"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"


class DispatchState(str, enum.Enum):
    """``public.dispatch_state`` — the responder workflow, unchanged from 0001.

    The order here is the order a dispatch moves through. ``STOOD_DOWN`` is a
    terminal exit for "no longer needed" and is deliberately not a step on the
    way to ``CLOSED``: a dispatch that was stood down did not reach a scene, and
    collapsing the two would lose that.
    """

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    DISPATCHED = "dispatched"
    ON_SCENE = "on_scene"
    STOOD_DOWN = "stood_down"
    CLOSED = "closed"

    @property
    def is_terminal(self) -> bool:
        return self in (DispatchState.STOOD_DOWN, DispatchState.CLOSED)


class LocationResolution(str, enum.Enum):
    """How a corroborating signal compared with the selected campus location.

    Four named states, no numeric score. A confidence number would need
    calibration data this project does not have, and "0.82" reads as measurement
    when it would be a guess.

    None of these ever changes where the incident is. ``core.report.location_id``
    is the operational truth; this records what the evidence had to say about it.
    """

    CORROBORATED = "corroborated"
    APPROXIMATE = "approximate"
    CONFLICTING = "conflicting"
    UNRESOLVED = "unresolved"


class LocationSignalSource(str, enum.Enum):
    PHOTO_EXIF = "photo_exif"
    DEVICE_GPS = "device_gps"
    LOCATION_DEFAULT = "location_default"


class MlTask(str, enum.Enum):
    CLASSIFICATION = "classification"
    SIMILARITY = "similarity"
    CLUSTERING = "clustering"
    RISK_SCORING = "risk_scoring"


class ModelFamily(str, enum.Enum):
    RULE_BASED = "rule_based"
    BASELINE = "baseline"
    TRANSFORMER = "transformer"


class LinkType(str, enum.Enum):
    """How two reports relate.

    ``DUPLICATE`` means "the same event, reported twice". ``SAME_PATTERN`` means
    "different events that look like the same problem" — which is what feeds
    clustering, and is a much weaker claim.
    """

    DUPLICATE = "duplicate"
    RELATED = "related"
    SAME_PATTERN = "same_pattern"


class LinkReview(str, enum.Enum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class NotificationAudience(str, enum.Enum):
    """Who a notification is addressed to.

    This project writes ``USER`` only. ``ROLE``, ``ZONE``, and
    ``ALL_STUDENTS`` exist in the schema for `notify.broadcast_alert`-style
    campus advisories — an institutional-policy feature (who may issue one,
    under what authority) that this phase does not build.
    """

    USER = "user"
    ROLE = "role"
    ZONE = "zone"
    ALL_STUDENTS = "all_students"


class NotificationCategory(str, enum.Enum):
    STATUS_UPDATE = "status_update"
    ASSIGNMENT = "assignment"
    ALERT = "alert"
    SYSTEM = "system"


class DeliveryState(str, enum.Enum):
    """Where a notification is in its own lifecycle.

    Not a proxy for "was this pushed to a device." No Firebase Cloud Messaging
    credentials are configured anywhere in this deployment, and
    `Notification.fcm_message_id` stays `None` for every row this project
    writes — that is the honest signal that push never happened. `SENT` here
    means "written and available through the authenticated API," which is a
    real, working delivery channel (the in-app notification list) and not a
    claim about any other one.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class StorageBackend(str, enum.Enum):
    FIREBASE_STORAGE = "firebase_storage"
    LOCAL = "local"


class AuditOutcome(str, enum.Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class PolicyOrigin(str, enum.Enum):
    PROTOTYPE_DEFAULT = "prototype_default"
    INSTITUTIONAL_REQUIREMENT = "institutional_requirement"
    REGULATORY = "regulatory"


def pg_enum(python_enum: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        python_enum,
        name=name,
        schema="public",
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )
