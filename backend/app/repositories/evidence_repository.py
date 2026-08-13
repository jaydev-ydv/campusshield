"""Data access for pending uploads and evidence objects.

The claim path is the interesting one. Moving a row from `pending_upload` to
`evidence_object` is the moment an image becomes evidence, and it happens inside
the report-creation transaction — so either the report and all its evidence
exist, or neither does.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import EvidenceObject, PendingUpload
from ..models.enums import StorageBackend


class EvidenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- pending -----------------------------------------------------------

    def add_pending(
        self,
        *,
        token_hash: str,
        storage_backend: StorageBackend,
        storage_path: str,
        content_type: str,
        byte_size: int,
        sha256: str,
        original_sha256: str,
        image_width: int,
        image_height: int,
        ttl_hours: int,
        exif_latitude: float | None = None,
        exif_longitude: float | None = None,
        exif_captured_at: datetime | None = None,
    ) -> PendingUpload:
        pending = PendingUpload(
            token_hash=token_hash,
            storage_backend=storage_backend,
            storage_path=storage_path,
            content_type=content_type,
            byte_size=byte_size,
            sha256=sha256,
            original_sha256=original_sha256,
            image_width=image_width,
            image_height=image_height,
            exif_latitude=exif_latitude,
            exif_longitude=exif_longitude,
            exif_captured_at=exif_captured_at,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        )
        self._session.add(pending)
        self._session.flush()
        return pending

    def get_pending(self, token_hash: str) -> PendingUpload | None:
        """Resolve a capability token to its upload.

        Expired rows are treated as absent. The reaper deletes them and the
        storage object together; until it runs, an expired token must not work.
        """
        pending = self._session.scalar(
            select(PendingUpload).where(PendingUpload.token_hash == token_hash)
        )
        if pending is None:
            return None
        if pending.expires_at <= datetime.now(timezone.utc):
            return None
        return pending

    def discard_pending(self, pending: PendingUpload) -> None:
        self._session.delete(pending)
        self._session.flush()

    def expired_pending(self, limit: int = 500) -> list[PendingUpload]:
        return list(
            self._session.scalars(
                select(PendingUpload)
                .where(PendingUpload.expires_at <= datetime.now(timezone.utc))
                .limit(limit)
            )
        )

    def delete_pending_by_id(self, upload_id: uuid.UUID) -> None:
        self._session.execute(delete(PendingUpload).where(PendingUpload.upload_id == upload_id))

    # -- attachment --------------------------------------------------------

    def attach(self, pending: PendingUpload, report_id: uuid.UUID) -> EvidenceObject:
        """Turn a pending upload into evidence on a report.

        The pending row is deleted in the same transaction, so a token is
        single-use and the storage object has exactly one owner at all times.

        `original_filename` is **never** set. The column remains for schema
        compatibility and its anonymous-report trigger still stands, but no
        filename is persisted for any report now: a responder gains nothing from
        it, and a phone-chosen filename is a disclosure risk for identified
        reports too, not only anonymous ones.
        """
        evidence = EvidenceObject(
            report_id=report_id,
            storage_backend=pending.storage_backend,
            storage_path=pending.storage_path,
            content_type=pending.content_type,
            byte_size=pending.byte_size,
            sha256=pending.sha256,
            original_filename=None,
            original_sha256=pending.original_sha256,
            metadata_stripped_at=pending.metadata_stripped_at,
            image_width=pending.image_width,
            image_height=pending.image_height,
        )
        self._session.add(evidence)
        self._session.delete(pending)
        self._session.flush()
        return evidence

    # -- reads -------------------------------------------------------------

    def get_evidence(self, evidence_id: uuid.UUID) -> EvidenceObject | None:
        evidence = self._session.get(EvidenceObject, evidence_id)
        if evidence is None or evidence.is_purged:
            return None
        return evidence

    def list_for_report(self, report_id: uuid.UUID) -> list[EvidenceObject]:
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

    # -- retention -----------------------------------------------------------

    def expired_evidence(self, limit: int = 500) -> list[EvidenceObject]:
        """Attached evidence whose retention period has elapsed and whose bytes
        have not already been purged.

        ``retention_expires_at`` is stamped at insert by
        ``evidence.fn_evidence_retention_stamp()``, from the same
        ``core.system_policy`` value that produced ``retention_days_applied``.
        """
        return list(
            self._session.scalars(
                select(EvidenceObject)
                .where(
                    EvidenceObject.is_purged.is_(False),
                    EvidenceObject.retention_expires_at.isnot(None),
                    EvidenceObject.retention_expires_at <= datetime.now(timezone.utc),
                )
                .limit(limit)
            )
        )

    def mark_purged(self, evidence_id: uuid.UUID, *, reason: str) -> None:
        """Record that an evidence object's stored bytes have been deleted.

        The row itself is kept, not deleted: removing it would erase the
        record of what evidence a report once carried and why it is gone.
        ``is_purged`` is enough to make every read path treat it as absent —
        ``get_evidence`` and ``list_for_report`` already filter on it.
        """
        self._session.execute(
            update(EvidenceObject)
            .where(EvidenceObject.evidence_id == evidence_id)
            .values(
                is_purged=True,
                purged_at=datetime.now(timezone.utc),
                purge_reason=reason,
            )
        )
