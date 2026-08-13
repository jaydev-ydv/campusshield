"""Evidence upload, retrieval and cleanup.

Orchestrates: validate → sanitise → store → record. No Flask, no Firebase — the
storage provider arrives as a dependency, which is what lets the whole flow be
tested without a bucket while sanitisation, authorization and the database
constraints all run for real.

Two ordering rules that are not negotiable:

**Store before recording.** The database row is written only after the bytes are
in storage. The reverse order leaves a row pointing at nothing, and a responder
opening it gets an error instead of evidence.

**Delete on failure.** If the row cannot be written after a successful store, the
object is removed. An orphan in the bucket is a file nobody can reach, nobody can
audit, and no retention policy governs.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from ..errors import AppError, NotFoundError, ServiceUnavailableError, ValidationError
from ..models import EvidenceObject
from ..models.enums import StorageBackend
from ..repositories.evidence_repository import EvidenceRepository
from ..storage.paths import generate_storage_path
from ..storage.provider import StorageError, StorageProvider, StorageUnavailableError
from ..utils.exif_location import as_utc, extract_photo_location
from ..utils.image_sanitizer import ImageRejectedError, sanitise_image

logger = logging.getLogger(__name__)

TOKEN_BYTES = 16  # 128 bits


class EvidenceRejectedError(ValidationError):
    """The upload was refused. Carries the sanitiser's reason code."""

    code = "EVIDENCE_REJECTED"


def hash_token(raw: str) -> str:
    """SHA-256 of a capability token.

    Only the hash is stored, matching the treatment of anonymous report access
    tokens: a database dump yields hashes, not usable capabilities.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class UploadResult:
    """What the client is told about a successful upload.

    Deliberately narrow. The storage path is **not** included: a client that
    never learns a path cannot ask for one, and cannot be tricked into revealing
    one. The token is the only handle.
    """

    __slots__ = ("byte_size", "content_type", "height", "token", "width")

    def __init__(self, *, token: str, content_type: str, byte_size: int, width: int, height: int):
        self.token = token
        self.content_type = content_type
        self.byte_size = byte_size
        self.width = width
        self.height = height


class EvidenceService:
    def __init__(
        self,
        *,
        evidence: EvidenceRepository,
        storage: StorageProvider,
        max_upload_bytes: int,
        pending_ttl_hours: int,
    ) -> None:
        self._evidence = evidence
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes
        self._pending_ttl_hours = pending_ttl_hours

    # -- upload ------------------------------------------------------------

    def upload(self, data: bytes, *, declared_content_type: str | None = None) -> UploadResult:
        """Validate, sanitise, store, and stage the image.

        No report is involved. Evidence is uploaded before the report exists, and
        attachment happens at submission — which is also why authorization is
        inherently correct: an upload can only ever be attached to a report the
        caller is in the act of creating.
        """
        try:
            sanitised = sanitise_image(
                data,
                declared_content_type=declared_content_type,
                max_bytes=self._max_upload_bytes,
            )
        except ImageRejectedError as exc:
            raise EvidenceRejectedError(str(exc), details={"reason": exc.reason}) from exc

        # Read GPS from the ORIGINAL bytes, before they are discarded. This is
        # the only moment it exists: `sanitised.data` has had every scrap of
        # metadata destroyed, which is the point. Extraction and destruction are
        # two steps in one direction — the coordinate goes to the location plane,
        # the image goes to storage carrying nothing.
        #
        # Absence is the ordinary result and is never treated as suspicious.
        photo_location = extract_photo_location(data)

        # Server-generated, opaque, and never influenced by the client. Nothing
        # in it derives from a uid, an email, a report, or a filename.
        path = generate_storage_path(sanitised.content_type)

        try:
            self._storage.put(path, sanitised.data, content_type=sanitised.content_type)
        except StorageUnavailableError as exc:
            logger.error("storage unavailable during evidence upload")
            raise ServiceUnavailableError(
                "Could not store the image right now. Please try again."
            ) from exc
        except StorageError as exc:
            logger.error("storage rejected an evidence object: %s", type(exc).__name__)
            raise ServiceUnavailableError("Could not store the image.") from exc

        raw_token = secrets.token_hex(TOKEN_BYTES)
        try:
            self._evidence.add_pending(
                token_hash=hash_token(raw_token),
                storage_backend=self._backend(),
                storage_path=path,
                content_type=sanitised.content_type,
                byte_size=sanitised.byte_size,
                sha256=sanitised.sha256,
                original_sha256=sanitised.original_sha256,
                image_width=sanitised.width,
                image_height=sanitised.height,
                ttl_hours=self._pending_ttl_hours,
                exif_latitude=photo_location.latitude if photo_location else None,
                exif_longitude=photo_location.longitude if photo_location else None,
                exif_captured_at=as_utc(photo_location.captured_at) if photo_location else None,
            )
        except Exception:
            # The bytes are stored but unreachable. Remove them rather than leave
            # a file no policy governs and no audit trail covers.
            self._storage.delete(path)
            raise

        # The coordinate itself is never logged — only whether one was present,
        # which is what an operator needs to understand traffic without the log
        # becoming a second copy of the location data.
        logger.info(
            "evidence uploaded (%s, %d bytes, %dx%d, location signal: %s)",
            sanitised.content_type,
            sanitised.byte_size,
            sanitised.width,
            sanitised.height,
            "present" if photo_location else "none",
        )
        return UploadResult(
            token=raw_token,
            content_type=sanitised.content_type,
            byte_size=sanitised.byte_size,
            width=sanitised.width,
            height=sanitised.height,
        )

    def discard(self, raw_token: str) -> bool:
        """Remove a staged upload — the wizard's "Remove" action.

        Storage first, then the row. If the object delete fails the row survives
        and the reaper will retry; the reverse would orphan the object silently.
        """
        pending = self._evidence.get_pending(hash_token(raw_token))
        if pending is None:
            return False
        self._storage.delete(pending.storage_path)
        self._evidence.discard_pending(pending)
        return True

    # -- attachment --------------------------------------------------------

    def attach_all(
        self, tokens: list[str], report_id: uuid.UUID, *, limit: int
    ) -> list[EvidenceObject]:
        """Claim staged uploads onto a report, inside the caller's transaction.

        Every token must resolve. A partial attachment would leave the student
        believing an image was included when it was not — worse than refusing the
        submission and letting them retry.
        """
        if not tokens:
            return []
        if len(tokens) > limit:
            raise ValidationError(
                f"At most {limit} images per report.",
                details={"fields": {"evidence_tokens": ["Too many images."]}},
            )
        if len(set(tokens)) != len(tokens):
            raise ValidationError(
                "The same image was attached twice.",
                details={"fields": {"evidence_tokens": ["Duplicate token."]}},
            )

        attached: list[EvidenceObject] = []
        for token in tokens:
            pending = self._evidence.get_pending(hash_token(token))
            if pending is None:
                # Unknown, already claimed, or expired. Not distinguished: the
                # difference tells a caller whether a token they do not hold is
                # real.
                raise ValidationError(
                    "One of the attached images is no longer available. Please add it again.",
                    details={"fields": {"evidence_tokens": ["Unknown or expired token."]}},
                )
            attached.append(self._evidence.attach(pending, report_id))
        return attached

    # -- retrieval ---------------------------------------------------------

    def read_bytes(self, evidence: EvidenceObject) -> bytes:
        """Fetch the stored image for an already-authorised caller.

        Authorization happens in the route, against the report — this method is
        reached only after `can_view_report` has admitted the caller.
        """
        try:
            return self._storage.get(evidence.storage_path)
        except StorageError as exc:
            logger.error("stored evidence could not be read: %s", type(exc).__name__)
            raise ServiceUnavailableError("The image could not be retrieved.") from exc

    def get_for_report(self, evidence_id: uuid.UUID) -> EvidenceObject:
        evidence = self._evidence.get_evidence(evidence_id)
        if evidence is None:
            raise NotFoundError("No such evidence.")
        return evidence

    # -- cleanup -----------------------------------------------------------

    def reap_expired(self, *, limit: int = 500) -> int:
        """Delete abandoned uploads, object and row together.

        Intended for a scheduled job. An upload that was never attached is not
        evidence and must not linger on evidence retention.
        """
        removed = 0
        for pending in self._evidence.expired_pending(limit=limit):
            self._storage.delete(pending.storage_path)
            self._evidence.delete_pending_by_id(pending.upload_id)
            removed += 1
        if removed:
            logger.info("reaped %d abandoned upload(s)", removed)
        return removed

    def purge_expired(self, *, limit: int = 500, reason: str = "retention_expiry") -> int:
        """Delete the storage bytes for attached evidence past its retention
        period. Intended for a scheduled job, alongside :meth:`reap_expired`.

        The database row survives, marked ``is_purged`` — a report's record of
        what evidence it once carried is not itself erased, only the bytes and
        the ability to view them. ``storage.delete`` is idempotent for an
        object that is already gone, so re-running this after a partial
        failure is always safe.

        ``reason`` must be one of the values ``ck_evidence_purge_reason``
        permits (``retention_expiry``, ``withdrawn``, ``admin_request``) — the
        database enforces this regardless of what a caller passes.
        """
        purged = 0
        for evidence in self._evidence.expired_evidence(limit=limit):
            self._storage.delete(evidence.storage_path)
            self._evidence.mark_purged(evidence.evidence_id, reason=reason)
            purged += 1
        if purged:
            logger.info("purged %d evidence object(s) past retention", purged)
        return purged

    def _backend(self) -> StorageBackend:
        return (
            StorageBackend.FIREBASE_STORAGE
            if self._storage.name == "firebase_storage"
            else StorageBackend.LOCAL
        )


__all__ = ["AppError", "EvidenceRejectedError", "EvidenceService", "UploadResult", "hash_token"]
