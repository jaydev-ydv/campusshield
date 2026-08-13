"""Wiring services from the request-scoped database session.

Constructed per request rather than at import time, because each carries a
SQLAlchemy ``Session`` whose lifetime is the request.  This is also the only
place routes learn which repositories a service needs — a route asks for a
service and gets one assembled.
"""

from __future__ import annotations

from flask import current_app
from sqlalchemy.orm import Session

from ..extensions import db
from ..repositories.audit_repository import AuditRepository
from ..repositories.case_repository import CaseRepository
from ..repositories.catalog_repository import (
    CategoryRepository,
    LocationRepository,
    PolicyRepository,
)
from ..repositories.evidence_repository import EvidenceRepository
from ..repositories.health_repository import HealthRepository
from ..repositories.incident_repository import IncidentRepository
from ..repositories.ml_repository import MlRepository
from ..repositories.notification_repository import NotificationRepository
from ..repositories.report_repository import ReportRepository, ReportTokenRepository
from ..services.account_service import AccountService
from ..services.case_service import CaseService
from ..services.catalog_service import CatalogService
from ..services.evidence_service import EvidenceService
from ..services.health_service import HealthService
from ..services.incident_service import IncidentService
from ..services.notification_service import NotificationService
from ..services.report_service import ReportService
from ..services.triage_service import TriageService


def _session() -> Session:
    """The request-scoped session, typed as a plain ``Session``.

    ``db.session`` is a ``scoped_session`` proxy: it forwards every ``Session``
    method but is not a subclass, so a static checker rejects passing it where a
    ``Session`` is expected. Repositories take a real ``Session`` so they can be
    constructed in tests without Flask, and the one cast lives here rather than
    being repeated at every call site.
    """
    return db.session  # type: ignore[return-value]


def evidence_repository() -> EvidenceRepository:
    return EvidenceRepository(_session())


def report_repository() -> ReportRepository:
    return ReportRepository(_session())


def report_token_repository() -> ReportTokenRepository:
    return ReportTokenRepository(_session())


def evidence_service() -> EvidenceService:
    """Built from the storage provider installed at startup.

    The provider is an application extension, not a module global, so a test can
    swap it without touching anything else.
    """
    return EvidenceService(
        evidence=EvidenceRepository(_session()),
        storage=current_app.extensions["storage_provider"],
        max_upload_bytes=current_app.config["MAX_IMAGE_UPLOAD_BYTES"],
        pending_ttl_hours=current_app.config["PENDING_UPLOAD_TTL_HOURS"],
    )


def triage_service() -> TriageService:
    """Built around the artifact loaded once at startup.

    `model_bundle` is None when no artifact is installed, which is a supported
    state: risk scoring still runs (it reads the incident, not the narrative) and
    classification is simply absent.
    """
    return TriageService(
        ml=MlRepository(_session()),
        campus_timezone=current_app.config["CAMPUS_TIMEZONE"],
        bundle=current_app.extensions.get("model_bundle"),
    )


def case_repository() -> CaseRepository:
    return CaseRepository(_session())


def notification_repository() -> NotificationRepository:
    return NotificationRepository(_session())


def notification_service() -> NotificationService:
    return NotificationService(
        notifications=notification_repository(), reports=ReportRepository(_session())
    )


def case_service() -> CaseService:
    return CaseService(
        cases=case_repository(),
        reports=ReportRepository(_session()),
        notifications=notification_service(),
    )


def incident_service() -> IncidentService:
    return IncidentService(
        incidents=IncidentRepository(_session()),
        reports=ReportRepository(_session()),
        triage=triage_service(),
        cases=case_service(),
    )


def account_service() -> AccountService:
    return AccountService(_session())


def health_service() -> HealthService:
    return HealthService(health=HealthRepository(_session()))


def catalog_service() -> CatalogService:
    return CatalogService(
        locations=LocationRepository(_session()),
        categories=CategoryRepository(_session()),
    )


def report_service() -> ReportService:
    return ReportService(
        reports=ReportRepository(_session()),
        evidence=EvidenceRepository(_session()),
        locations=LocationRepository(_session()),
        categories=CategoryRepository(_session()),
        policies=PolicyRepository(_session()),
        tokens=ReportTokenRepository(_session()),
        campus_timezone=current_app.config["CAMPUS_TIMEZONE"],
        max_evidence=current_app.config["MAX_EVIDENCE_PER_REPORT"],
        corroboration_radius_m=PolicyRepository(_session()).get_int(
            "location_corroboration_radius_m", 150
        ),
        triage=triage_service(),
    )


def audit_repository() -> AuditRepository:
    return AuditRepository(_session())
