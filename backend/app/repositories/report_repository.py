"""Data access for reports, narratives, attribution, tokens, and evidence.

Every query that could cross the identity boundary lives here, so the boundary
can be audited by reading one file.

Two rules this module keeps:

* **Attribution is written only for identified reports.**  The database enforces
  it with a trigger; :meth:`ReportRepository.attribute` refuses first so the
  failure is a clean 409 rather than a constraint error.
* **``user_id`` never leaves.**  Methods return reports, or booleans, or an
  access context — never an attribution row.  ``ReportAccessContext`` carries
  ``reporter_user_id`` purely so the authorisation policy can compare it against
  the caller, and serializers never see it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from ..models import (
    CaseStatusHistory,
    EvidenceObject,
    Report,
    ReportAccessToken,
    ReportAttribution,
    ReportCategory,
    ReportLocationDetail,
    ReportNarrative,
    SubmissionQuota,
)
from ..models.enums import ReportStatus, StorageBackend, SubmissionMode
from ..security.authorization import ReportAccessContext
from ..services.location_service import ResolvedLocation


class ReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- writes ------------------------------------------------------------

    def add(self, report: Report) -> Report:
        self._session.add(report)
        self._session.flush()
        return report

    def add_narrative(self, report_id: uuid.UUID, text: str) -> ReportNarrative:
        """Retention columns are deliberately not set.

        A database trigger stamps them from ``core.system_policy`` at insert, and
        another refuses to let them change afterwards.  Setting them here would
        let an application bug quietly apply the wrong retention terms to
        somebody's account of what happened to them.
        """
        narrative = ReportNarrative(
            report_id=report_id,
            narrative=text,
            word_count=len(text.split()),
        )
        self._session.add(narrative)
        self._session.flush()
        return narrative

    def attribute(
        self,
        report: Report,
        user_id: uuid.UUID,
        *,
        contact_consent: bool = True,
    ) -> ReportAttribution:
        """Link an identified report to its reporter.

        Refuses anonymous reports before touching the database.  The trigger
        would refuse too, but a service-layer guard turns a constraint violation
        into an explicit, testable rule.
        """
        if report.submission_mode is not SubmissionMode.IDENTIFIED:
            raise ValueError(
                "refusing to attribute an anonymous report; anonymity is the absence of this row"
            )
        attribution = ReportAttribution(
            report_id=report.report_id,
            user_id=user_id,
            contact_consent=contact_consent,
        )
        self._session.add(attribution)
        self._session.flush()
        return attribution

    def add_access_token(
        self, report_id: uuid.UUID, token_hash: str, expires_at: datetime | None
    ) -> ReportAccessToken:
        token = ReportAccessToken(report_id=report_id, token_hash=token_hash, expires_at=expires_at)
        self._session.add(token)
        self._session.flush()
        return token

    def add_evidence(
        self,
        report_id: uuid.UUID,
        *,
        storage_backend: StorageBackend,
        storage_path: str,
        content_type: str,
        byte_size: int,
        sha256: str | None,
        original_filename: str | None,
    ) -> EvidenceObject:
        """``original_filename`` must already be stripped for anonymous reports.

        The service does that; a trigger rejects it if the service ever forgets.
        No uploader is recorded here or anywhere — that column does not exist.
        """
        evidence = EvidenceObject(
            report_id=report_id,
            storage_backend=storage_backend,
            storage_path=storage_path,
            content_type=content_type,
            byte_size=byte_size,
            sha256=sha256,
            original_filename=original_filename,
        )
        self._session.add(evidence)
        self._session.flush()
        return evidence

    def set_location_detail(
        self, report_id: uuid.UUID, resolved: ResolvedLocation
    ) -> ReportLocationDetail:
        """Record how a corroborating signal compared with the selected location.

        Written once, at submission. This never touches ``core.report.location_id``
        — the student's selection is the operational location and this row only
        describes what the evidence had to say about it.
        """
        detail = ReportLocationDetail(
            report_id=report_id,
            resolution=resolved.resolution,
            source=resolved.source,
            signal_latitude=resolved.signal_latitude,
            signal_longitude=resolved.signal_longitude,
            distance_m=resolved.distance_m,
            signal_captured_at=resolved.signal_captured_at,
            conflict_note=resolved.conflict_note,
        )
        self._session.add(detail)
        self._session.flush()
        return detail

    def record_initial_status(self, report_id: uuid.UUID) -> None:
        """Open the append-only status trail.

        ``core.report.current_status`` already defaults to ``submitted``; this
        writes the first history row so the trail starts at submission rather
        than at whatever an authority does first.
        """
        self._session.add(
            CaseStatusHistory(
                report_id=report_id,
                from_status=None,
                to_status=ReportStatus.SUBMITTED,
                changed_by=None,
                remark="Report submitted.",
                visible_to_reporter=True,
            )
        )
        self._session.flush()

    def increment_quota(self, user_id: uuid.UUID, on_date: date) -> int:
        """Count a submission against the caller's daily quota.

        A counter, not a link: this records that the user filed something today,
        never what.  It is how anonymous submissions are rate-limited without
        rebuilding the connection anonymity exists to prevent.
        """
        row = self._session.get(SubmissionQuota, (user_id, on_date))
        if row is None:
            row = SubmissionQuota(user_id=user_id, quota_date=on_date, submitted_count=1)
            self._session.add(row)
        else:
            row.submitted_count += 1
        self._session.flush()
        return row.submitted_count

    def quota_used(self, user_id: uuid.UUID, on_date: date) -> int:
        row = self._session.get(SubmissionQuota, (user_id, on_date))
        return row.submitted_count if row else 0

    # -- reads -------------------------------------------------------------

    def get_by_id(self, report_id: uuid.UUID) -> Report | None:
        return self._session.get(Report, report_id)

    def get_by_public_ref(self, public_ref: str) -> Report | None:
        return self._session.scalar(select(Report).where(Report.public_ref == public_ref))

    def public_ref_exists(self, public_ref: str) -> bool:
        return (
            self._session.scalar(
                select(func.count()).select_from(Report).where(Report.public_ref == public_ref)
            )
            or 0
        ) > 0

    def _owned_by(self, user_id: uuid.UUID) -> Select[tuple[Report]]:
        """Reports the given user is recorded as having filed.

        Joins ``identity.report_attribution``, which by construction contains no
        anonymous report.  A user's anonymous submissions are therefore absent
        from their own list — not a gap but the guarantee working: if this
        endpoint could find them, so could anything else with the same access.
        """
        return (
            select(Report)
            .join(ReportAttribution, ReportAttribution.report_id == Report.report_id)
            .where(ReportAttribution.user_id == user_id)
        )

    def list_for_reporter(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[Report]:
        stmt = (
            self._owned_by(user_id).order_by(Report.submitted_at.desc()).limit(limit).offset(offset)
        )
        return list(self._session.scalars(stmt).unique())

    def count_for_reporter(self, user_id: uuid.UUID) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(ReportAttribution)
                .where(ReportAttribution.user_id == user_id)
            )
            or 0
        )

    def get_narrative(self, report_id: uuid.UUID) -> ReportNarrative | None:
        """Not a relationship on ``Report``, on purpose.

        Lazy-loading would make narrative access a property read that any
        serializer could trip over.  A separate call forces every caller past an
        authorisation check first.
        """
        return self._session.get(ReportNarrative, report_id)

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

    def visible_status_history(self, report_id: uuid.UUID) -> list[CaseStatusHistory]:
        """Only rows marked visible to the reporter.

        ``visible_to_reporter`` is what lets an ICC member keep an internal
        handling note separate from what the student sees, without a second table
        and without trusting the frontend to filter.
        """
        return list(
            self._session.scalars(
                select(CaseStatusHistory)
                .where(
                    CaseStatusHistory.report_id == report_id,
                    CaseStatusHistory.visible_to_reporter.is_(True),
                )
                .order_by(CaseStatusHistory.changed_at)
            )
        )

    def access_context(self, report: Report, *, via_token: bool = False) -> ReportAccessContext:
        """Assemble the facts the authorisation policy needs.

        The one place ``report_attribution.user_id`` is read outside an explicit
        identity-disclosure flow, and it goes straight into a comparison against
        the caller — never into a response.  For anonymous reports the lookup
        returns nothing, so the policy is handed ``None`` and cannot leak what it
        was never given.
        """
        reporter_user_id: uuid.UUID | None = None
        if report.submission_mode is SubmissionMode.IDENTIFIED:
            reporter_user_id = self._session.scalar(
                select(ReportAttribution.user_id).where(
                    ReportAttribution.report_id == report.report_id
                )
            )

        category = (
            self._session.get(ReportCategory, report.declared_category_id)
            if report.declared_category_id is not None
            else None
        )

        return ReportAccessContext(
            report_id=report.report_id,
            routes_to_role=category.routes_to_role if category else None,
            requires_confidentiality=bool(category and category.requires_confidentiality),
            reporter_user_id=reporter_user_id,
            assigned_to_user_id=self._active_assignee(report.report_id),
            accessed_via_token=via_token,
        )

    def _active_assignee(self, report_id: uuid.UUID) -> uuid.UUID | None:
        # core.case_assignment is not mapped in Phase 1 (no assignment endpoints
        # exist yet), so this is read as a narrow scalar rather than through a
        # model.  Returning None simply means "no assignment-based access", which
        # is the correct answer while assignment is unimplemented.
        from sqlalchemy import text as sql_text

        return self._session.scalar(
            sql_text(
                "SELECT assigned_to FROM core.case_assignment "
                "WHERE report_id = :rid AND is_active LIMIT 1"
            ),
            {"rid": str(report_id)},
        )


class ReportTokenRepository:
    """Lookup and bookkeeping for anonymous status-access tokens."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(self, token_hash: str) -> ReportAccessToken | None:
        now = datetime.now(timezone.utc)
        token = self._session.get(ReportAccessToken, token_hash)
        if token is None or token.revoked_at is not None:
            return None
        if token.expires_at is not None and token.expires_at <= now:
            return None
        return token

    def record_use(self, token_hash: str) -> None:
        self._session.execute(
            update(ReportAccessToken)
            .where(ReportAccessToken.token_hash == token_hash)
            .values(
                last_used_at=datetime.now(timezone.utc),
                use_count=ReportAccessToken.use_count + 1,
            )
        )
