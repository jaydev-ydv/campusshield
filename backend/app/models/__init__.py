"""SQLAlchemy models mapped onto the schema created by Alembic.

These models are a **mapping, never a source of truth**.  The schema is defined
by ``migrations/versions/0001_initial_schema.py`` and includes triggers, partial
unique indexes, and CHECK constraints that SQLAlchemy cannot express.  Nothing
here is used to create, alter, or drop a table: ``db.create_all()`` must never be
called, and Alembic autogenerate stays off.

Only the tables Phase 1 touches are mapped.  The remaining 20-odd tables exist in
the database and will be mapped as the features that need them are built.

Deliberate absences, each of which is a privacy guarantee rather than an
oversight:

* ``Report`` has no ``user_id`` and no relationship to ``AppUser``.  Attribution
  lives in ``ReportAttribution`` and exists only for identified reports.
* ``EvidenceObject`` has no uploader.
* ``Report`` does not lazy-load its narrative; narrative access goes through the
  repository so it can be authorised, not merely convenient.
"""

from .app_user import AppUser
from .campus import CampusLocation, CampusZone
from .dispatch import EmergencyDispatch, ReportLocationDetail
from .evidence import EvidenceObject, PendingUpload
from .ml import (
    ModelVersion,
    ReportClassification,
    ReportEmbedding,
    ReportLink,
    RiskAssessment,
)
from .notify import Notification
from .policy import SystemPolicy
from .report import (
    CaseAssignment,
    CaseStatusHistory,
    Report,
    ReportAccessToken,
    ReportAttribution,
    ReportCategory,
    ReportNarrative,
    SubmissionQuota,
)

__all__ = [
    "AppUser",
    "CampusLocation",
    "CampusZone",
    "CaseAssignment",
    "CaseStatusHistory",
    "EmergencyDispatch",
    "EvidenceObject",
    "ModelVersion",
    "Notification",
    "PendingUpload",
    "Report",
    "ReportAccessToken",
    "ReportAttribution",
    "ReportCategory",
    "ReportClassification",
    "ReportEmbedding",
    "ReportLink",
    "ReportLocationDetail",
    "ReportNarrative",
    "RiskAssessment",
    "SubmissionQuota",
    "SystemPolicy",
]
