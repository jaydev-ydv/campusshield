from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base
from .enums import StorageBackend, pg_enum


class EvidenceObject(Base):
    """``evidence.evidence_object`` — a pointer to object storage.

    **No uploader column, by design.**  An uploader foreign key would silently
    de-anonymise every anonymous report that carried a photo, which is precisely
    the failure the whole separation exists to prevent.

    ``original_filename`` is dropped for anonymous reports.  The service layer
    strips it; a database trigger refuses it as the backstop.  Phone filenames
    leak more than people expect — ``IMG_20260810_Priya_hostel.jpg`` names a
    person, a place, and a date.

    ``storage_path`` is an opaque bucket path and must never reach a client.
    Files are served through short-lived signed URLs minted after an
    authorisation check, which is Phase 2 work.
    """

    __tablename__ = "evidence_object"
    __table_args__ = {"schema": "evidence"}

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.report.report_id", ondelete="CASCADE"), nullable=False
    )
    storage_backend: Mapped[StorageBackend] = mapped_column(
        pg_enum(StorageBackend, "storage_backend"), nullable=False
    )
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String)
    original_filename: Mapped[str | None] = mapped_column(String)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    retention_policy_key: Mapped[str | None] = mapped_column(String)
    retention_days_applied: Mapped[int | None] = mapped_column(Integer)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_purged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_reason: Mapped[str | None] = mapped_column(String)

    # Recorded by sanitisation. `original_sha256` is the hash of what was
    # uploaded; the original bytes themselves never reach the bucket.
    original_sha256: Mapped[str | None] = mapped_column(String)
    metadata_stripped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)


class PendingUpload(Base):
    """``evidence.pending_upload`` — an image stored but not yet attached to a report.

    Exists because evidence is uploaded before the report it belongs to. An
    abandoned upload is **not evidence**: it has a lifetime measured in hours,
    and giving it a row in ``evidence_object`` would hand it the 365-day evidence
    retention and break that table's "always attached to a report" invariant.

    **No user column, deliberately.** The upload is claimed with a capability
    token, exactly as an anonymous reporter claims their report. Recording an
    uploader would create a link between a person and an image that outlives the
    request — for a row that exists precisely because the report does not yet.
    Only the SHA-256 of the token is stored.
    """

    __tablename__ = "pending_upload"
    __table_args__ = {"schema": "evidence"}

    upload_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    storage_backend: Mapped[StorageBackend] = mapped_column(
        pg_enum(StorageBackend, "storage_backend"), nullable=False
    )
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    original_sha256: Mapped[str] = mapped_column(String, nullable=False)
    # Staging for the location signal, not evidence metadata. Read from the
    # photograph's EXIF at upload, moved to core.report_location_detail on
    # attachment, and deleted with this row either way. It is deliberately NOT
    # copied onto evidence.evidence_object: a reporter's coordinate must not
    # live for the retention period in the table responders read.
    exif_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    exif_longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    exif_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    image_width: Mapped[int] = mapped_column(Integer, nullable=False)
    image_height: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_stripped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
