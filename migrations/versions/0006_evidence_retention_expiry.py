"""Compute evidence.evidence_object.retention_expires_at at insert (Phase 4F).

## The gap this closes

`retention_days_applied` has been stamped from `core.system_policy` since
Phase 4B-1, by `evidence.fn_evidence_retention_stamp()`. `retention_expires_at`
— the column an actual purge would filter on, and the column
`ix_evidence_retention` was built to index — has existed since the same
migration but was never assigned a value by anything. Every row's expiry sat
permanently NULL, which is indistinguishable from "never expires" to any
query, including the partial index's own predicate (`WHERE NOT is_purged`,
which says nothing about a NULL expiry).

## What changes

`fn_evidence_retention_stamp()` gains one more line: once
`retention_days_applied` is resolved (explicit value or the policy default,
exactly as before), `retention_expires_at` is computed as `NEW.uploaded_at +
retention_days_applied days`. `uploaded_at` is available at this point because
PostgreSQL fills in column DEFAULTs before a BEFORE ROW trigger runs, not
after — confirmed directly against a real table in this phase's own testing,
not assumed from documentation.

`COALESCE`d against an explicit value exactly as `retention_days_applied`
already is, so a caller that sets its own expiry (there is none today, but the
trigger already extends this courtesy to `retention_days_applied`) is not
overridden.

No new column, no new table: the column and its index already existed,
unused. This migration only makes the value that was always meant to live
there actually get written.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

NEW_FUNCTION = """
CREATE OR REPLACE FUNCTION evidence.fn_evidence_retention_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_days INTEGER;
BEGIN
    IF NEW.retention_policy_key IS NULL THEN
        NEW.retention_policy_key := 'evidence_retention_days';
    END IF;
    SELECT policy_value::INTEGER INTO v_days
      FROM core.system_policy WHERE policy_key = NEW.retention_policy_key;
    IF v_days IS NULL THEN
        RAISE EXCEPTION 'retention policy % is not defined in core.system_policy',
            NEW.retention_policy_key USING ERRCODE = 'foreign_key_violation';
    END IF;
    NEW.retention_days_applied := COALESCE(NEW.retention_days_applied, v_days);
    NEW.retention_expires_at := COALESCE(
        NEW.retention_expires_at,
        NEW.uploaded_at + make_interval(days => NEW.retention_days_applied)
    );
    RETURN NEW;
END;
$$
"""

OLD_FUNCTION = """
CREATE OR REPLACE FUNCTION evidence.fn_evidence_retention_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_days INTEGER;
BEGIN
    IF NEW.retention_policy_key IS NULL THEN
        NEW.retention_policy_key := 'evidence_retention_days';
    END IF;
    SELECT policy_value::INTEGER INTO v_days
      FROM core.system_policy WHERE policy_key = NEW.retention_policy_key;
    IF v_days IS NULL THEN
        RAISE EXCEPTION 'retention policy % is not defined in core.system_policy',
            NEW.retention_policy_key USING ERRCODE = 'foreign_key_violation';
    END IF;
    NEW.retention_days_applied := COALESCE(NEW.retention_days_applied, v_days);
    RETURN NEW;
END;
$$
"""

COMMENT = (
    "COMMENT ON FUNCTION evidence.fn_evidence_retention_stamp() IS "
    "'Stamps retention_policy_key, retention_days_applied and "
    "retention_expires_at at insert, from core.system_policy. Phase 4F: "
    "retention_expires_at is what app.services.evidence_service.purge_expired() "
    "filters on via ix_evidence_retention.'"
)


def upgrade() -> None:
    op.execute(NEW_FUNCTION)
    op.execute(COMMENT)


def downgrade() -> None:
    op.execute(OLD_FUNCTION)
