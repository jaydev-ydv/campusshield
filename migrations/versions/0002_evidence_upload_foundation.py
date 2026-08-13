"""Evidence upload foundation (Phase 4B-1).

Revision ID: 0002
Revises: 0001

Adds the staging table for uploads that have not yet been attached to a report,
and the columns that record what sanitisation did.

**Why a staging table rather than a nullable report_id.**

Evidence is uploaded at wizard step 5, before the report exists. The alternative
— making ``evidence.evidence_object.report_id`` nullable and adding a state
column — was rejected in PHASE_4B_ARCHITECTURE.md §B for two reasons. Every query
over evidence would then have to remember to exclude orphans, and an abandoned
upload would inherit the 365-day evidence retention. A file uploaded and never
submitted is not evidence: it has a different lifecycle (hours, not months) and
no report to inherit privacy treatment from.

Keeping ``evidence_object`` "always attached to a report" preserves an invariant
the rest of the codebase already relies on.

**No user column on pending_upload, deliberately.**

The upload is claimed with a capability token, exactly as an anonymous reporter
claims their own report with ``core.report_access_token``. Storing an uploader id
would create a link between a person and an image that outlives the request, for
a row that exists precisely because the report does not yet. Only the SHA-256 of
the token is stored; the raw token is returned once and never persisted.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


STATEMENTS = [
    # -- evidence.pending_upload -------------------------------------------
    """
    CREATE TABLE evidence.pending_upload (
        upload_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        -- SHA-256 of a 128-bit random capability token. The raw token is
        -- returned to the uploader once and never stored, matching the
        -- treatment of core.report_access_token.
        token_hash           TEXT NOT NULL,
        storage_backend      public.storage_backend NOT NULL,
        -- Server-generated, opaque, and unrelated to any identity. A client
        -- cannot supply or influence this value.
        storage_path         TEXT NOT NULL,
        content_type         TEXT NOT NULL,
        byte_size            BIGINT NOT NULL,
        -- Hash of the SANITISED bytes actually stored.
        sha256               TEXT NOT NULL,
        -- Hash of what was uploaded, before metadata was stripped. Kept for
        -- integrity only; the original bytes are never stored anywhere.
        original_sha256      TEXT NOT NULL,
        image_width          INTEGER NOT NULL,
        image_height         INTEGER NOT NULL,
        metadata_stripped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        -- Short. An unclaimed upload is reaped, row and storage object together.
        expires_at           TIMESTAMPTZ NOT NULL,
        CONSTRAINT ck_pending_upload_token_is_sha256
            CHECK (token_hash ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_pending_upload_sha256
            CHECK (sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_pending_upload_original_sha256
            CHECK (original_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_pending_upload_byte_size
            CHECK (byte_size > 0 AND byte_size <= 26214400),
        CONSTRAINT ck_pending_upload_dimensions
            CHECK (image_width > 0 AND image_height > 0),
        CONSTRAINT ck_pending_upload_expiry
            CHECK (expires_at > created_at),
        -- Only the formats this phase supports. Video is deliberately absent;
        -- see PHASE_4B_ARCHITECTURE.md §B.
        CONSTRAINT ck_pending_upload_content_type
            CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp'))
    )
    """,
    "CREATE UNIQUE INDEX uq_pending_upload_token ON evidence.pending_upload (token_hash)",
    "CREATE UNIQUE INDEX uq_pending_upload_storage_path ON evidence.pending_upload (storage_path)",
    "CREATE INDEX ix_pending_upload_expiry ON evidence.pending_upload (expires_at)",
    # -- evidence.evidence_object ------------------------------------------
    #
    # Records what sanitisation did. Without these the system cannot show that
    # the stored bytes differ from what was uploaded, which is the claim the
    # whole privacy story rests on.
    "ALTER TABLE evidence.evidence_object ADD COLUMN original_sha256 TEXT",
    "ALTER TABLE evidence.evidence_object ADD COLUMN metadata_stripped_at TIMESTAMPTZ",
    "ALTER TABLE evidence.evidence_object ADD COLUMN image_width INTEGER",
    "ALTER TABLE evidence.evidence_object ADD COLUMN image_height INTEGER",
    """
    ALTER TABLE evidence.evidence_object
        ADD CONSTRAINT ck_evidence_original_sha256
        CHECK (original_sha256 IS NULL OR original_sha256 ~ '^[a-f0-9]{64}$')
    """,
    """
    ALTER TABLE evidence.evidence_object
        ADD CONSTRAINT ck_evidence_dimensions
        CHECK ((image_width IS NULL) = (image_height IS NULL)
               AND (image_width IS NULL OR (image_width > 0 AND image_height > 0)))
    """,
    # -- retention policy for abandoned uploads -----------------------------
    #
    # A row, not a column: the policy layer already exists and abandoned uploads
    # must never inherit evidence_retention_days.
    """
    INSERT INTO core.system_policy
        (policy_key, policy_value, value_type, origin, description, min_value, max_value)
    VALUES (
        'pending_upload_ttl_hours', '6', 'integer', 'prototype_default',
        'Hours before an uploaded image that was never attached to a report is deleted, '
        'row and storage object together. Deliberately short: an abandoned upload is not '
        'evidence and must not inherit evidence retention. PROTOTYPE POLICY.',
        '1', '168'
    )
    """,
    # -- comments -----------------------------------------------------------
    "COMMENT ON TABLE evidence.pending_upload IS "
    "'Uploads not yet attached to a report. Has NO user column by design: the upload "
    "is claimed with a capability token, so no link between a person and an image "
    "outlives the request. Reaped on expiry, row and storage object together.'",
    "COMMENT ON COLUMN evidence.pending_upload.storage_path IS "
    "'Server-generated and opaque. Contains no uid, email, report reference, or "
    "original filename. A client can neither supply nor influence it.'",
    "COMMENT ON COLUMN evidence.evidence_object.original_sha256 IS "
    "'Hash of the bytes as uploaded, before metadata was stripped. The original "
    "bytes themselves are never stored - only the sanitised image reaches the bucket.'",
    "COMMENT ON COLUMN evidence.evidence_object.metadata_stripped_at IS "
    "'When EXIF, GPS and device metadata were removed. NULL means the row predates "
    "sanitisation and its provenance is unknown.'",
]


DOWNGRADE_STATEMENTS = [
    "DELETE FROM core.system_policy WHERE policy_key = 'pending_upload_ttl_hours'",
    "ALTER TABLE evidence.evidence_object DROP CONSTRAINT IF EXISTS ck_evidence_dimensions",
    "ALTER TABLE evidence.evidence_object DROP CONSTRAINT IF EXISTS ck_evidence_original_sha256",
    "ALTER TABLE evidence.evidence_object DROP COLUMN IF EXISTS image_height",
    "ALTER TABLE evidence.evidence_object DROP COLUMN IF EXISTS image_width",
    "ALTER TABLE evidence.evidence_object DROP COLUMN IF EXISTS metadata_stripped_at",
    "ALTER TABLE evidence.evidence_object DROP COLUMN IF EXISTS original_sha256",
    "DROP TABLE IF EXISTS evidence.pending_upload",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
