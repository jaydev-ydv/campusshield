"""Location signal capture and responder dispatch (Phase 4B-2).

Two enums and one table, plus one policy row.

The table is `core.report_location_detail`, and it is a separate table for the
same reason `core.report_narrative` is: so `SELECT` can be revoked on it
independently. A precise incident coordinate is at least as sensitive as the
narrative — it says where a student physically was — and the analytics role must
be able to compute every hotspot without ever reading one. Putting the coordinate
on `core.report` would mean every serialiser, every view, and every future query
has to remember to exclude it. A separate table with its own grant makes
forgetting impossible rather than merely discouraged.

**No dispatch table is created.** `core.emergency_dispatch` already models the
responder workflow completely — the state machine, `acknowledged_by`, every
timestamp, `contact_attempted`, `public_note`. It needed a writer, not a
migration.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Enums
#
# Four named states rather than a numeric confidence score. There is no
# calibration dataset behind this system, so a number like "0.82 confident"
# would be invented precision dressed up as measurement. Each of these four has
# a rule a human can check.
# ---------------------------------------------------------------------------

ENUMS: list[tuple[str, list[str]]] = [
    (
        "location_resolution",
        [
            # A corroborating signal agrees with the selected campus location.
            "corroborated",
            # The selected location is mapped, but nothing corroborates the
            # precise point. This is the normal, unremarkable case.
            "approximate",
            # A signal disagrees with the selected location. Surfaced to a human,
            # never auto-resolved: a student who fled the scene before reporting
            # produces exactly the same reading as a forged coordinate.
            "conflicting",
            # Not enough information to say. Every report resolves here while the
            # campus survey is outstanding.
            "unresolved",
        ],
    ),
    (
        "location_signal_source",
        [
            # EXIF GPS from an uploaded photograph.
            "photo_exif",
            # The browser's Geolocation API at submission time. Not collected in
            # 4B-2; the value exists so adding it later is not a migration.
            "device_gps",
            # No corroborating signal; the campus location's own surveyed point.
            "location_default",
        ],
    ),
]


TABLE = """
CREATE TABLE core.report_location_detail (
    report_id       UUID PRIMARY KEY
                    REFERENCES core.report (report_id) ON DELETE CASCADE,

    resolution      public.location_resolution NOT NULL,
    source          public.location_signal_source NOT NULL,

    -- The corroborating signal's own coordinate, when there was one. This is
    -- NOT the incident location: core.report.location_id remains the operational
    -- truth, and nothing in this table overrides it. Stored so a responder can
    -- see *why* a conflict was raised.
    signal_latitude  NUMERIC(9, 6),
    signal_longitude NUMERIC(9, 6),

    -- Metres between the signal and the selected location's surveyed point.
    -- NULL when either side has no coordinate.
    distance_m      INTEGER,

    -- When the signal was captured — the shutter time for photo EXIF. May differ
    -- from the reported incident time, and may be absent or wrong.
    signal_captured_at TIMESTAMPTZ,

    -- Human-readable reason, written by the resolver for a responder to read.
    -- Never contains a raw EXIF dump.
    conflict_note   TEXT,

    resolved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_location_detail_coords_paired
        CHECK ((signal_latitude IS NULL) = (signal_longitude IS NULL)),
    CONSTRAINT ck_location_detail_latitude
        CHECK (signal_latitude IS NULL OR signal_latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_location_detail_longitude
        CHECK (signal_longitude IS NULL OR signal_longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_location_detail_distance_non_negative
        CHECK (distance_m IS NULL OR distance_m >= 0),

    -- A resolution that claims corroboration or conflict must name the signal
    -- that produced it. Without this, 'conflicting' could be written with no
    -- evidence behind it and no way for a responder to judge.
    CONSTRAINT ck_location_detail_signal_required
        CHECK (
            resolution NOT IN ('corroborated', 'conflicting')
            OR (signal_latitude IS NOT NULL AND source <> 'location_default')
        ),

    -- The converse: 'location_default' means there was no corroborating signal,
    -- so there is no signal coordinate to record.
    CONSTRAINT ck_location_detail_default_has_no_signal
        CHECK (source <> 'location_default' OR signal_latitude IS NULL)
)
"""


INDEXES = [
    # The responder queue filters on this to surface conflicts.
    "CREATE INDEX ix_location_detail_resolution ON core.report_location_detail (resolution)",
]


# ---------------------------------------------------------------------------
# Staging columns on evidence.pending_upload
#
# EXIF GPS is read at upload time, but it cannot be *resolved* then: the report
# does not exist yet, so there is no selected campus location to compare against.
# It has to wait somewhere between the two requests.
#
# `pending_upload` is the right place and not a compromise. It has no user
# column, it is unreachable without the capability token, and it is deleted
# within hours either by attachment or by the reaper — so a coordinate that never
# becomes part of a report does not survive the afternoon. The alternative,
# holding it on the evidence row, would park a reporter's coordinate in the table
# responders read, for the full retention period.
#
# On attachment these values move into core.report_location_detail and the
# staging row is deleted in the same transaction.
# ---------------------------------------------------------------------------

PENDING_COLUMNS = [
    "ALTER TABLE evidence.pending_upload ADD COLUMN exif_latitude NUMERIC(9, 6)",
    "ALTER TABLE evidence.pending_upload ADD COLUMN exif_longitude NUMERIC(9, 6)",
    "ALTER TABLE evidence.pending_upload ADD COLUMN exif_captured_at TIMESTAMPTZ",
    """
    ALTER TABLE evidence.pending_upload
        ADD CONSTRAINT ck_pending_upload_exif_coords_paired
        CHECK ((exif_latitude IS NULL) = (exif_longitude IS NULL))
    """,
    """
    ALTER TABLE evidence.pending_upload
        ADD CONSTRAINT ck_pending_upload_exif_range
        CHECK (exif_latitude IS NULL
               OR (exif_latitude BETWEEN -90 AND 90
                   AND exif_longitude BETWEEN -180 AND 180))
    """,
    "COMMENT ON COLUMN evidence.pending_upload.exif_latitude IS "
    "'Coordinate claimed by the photograph. Staging only: moved to "
    "core.report_location_detail on attachment and deleted with this row. Never "
    "copied to evidence.evidence_object, and never the incident location.'",
]


# `cs_analytics` is deliberately absent. The narrative firewall exists so
# hotspot analysis can run without reading what happened to a student; a precise
# coordinate deserves the same treatment, and granting it here would quietly
# undo that.
GRANTS = [
    "GRANT SELECT, INSERT ON core.report_location_detail TO cs_app",
]


POLICY_ROW = """
INSERT INTO core.system_policy
    (policy_key, policy_value, value_type, origin, description, min_value, max_value)
VALUES (
    'location_corroboration_radius_m', '150', 'integer', 'prototype_default',
    'Metres within which a corroborating signal is treated as agreeing with the '
    'selected campus location. Deliberately generous: consumer GPS is routinely '
    '5-50m out and worse indoors or among buildings, and a false conflict spends a '
    'responder''s attention on a report that was filed correctly. PROTOTYPE POLICY '
    '- not calibrated against survey data, because none exists yet.',
    '10', '2000'
)
ON CONFLICT (policy_key) DO NOTHING
"""


def _role_exists(role: str) -> bool:
    """Grants are skipped when the role is absent.

    Development and test databases are created without the application roles;
    production has them. The migration must apply cleanly to both.
    """
    return bool(
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        .scalar()
    )


def upgrade() -> None:
    for name, values in ENUMS:
        op.execute(
            "CREATE TYPE public.{} AS ENUM ({})".format(
                name, ", ".join("'{}'".format(v) for v in values)
            )
        )

    op.execute(TABLE)
    for statement in INDEXES:
        op.execute(statement)
    for statement in PENDING_COLUMNS:
        op.execute(statement)

    if _role_exists("cs_app"):
        for statement in GRANTS:
            op.execute(statement)

    op.execute(POLICY_ROW)


def downgrade() -> None:
    op.execute("DELETE FROM core.system_policy WHERE policy_key = 'location_corroboration_radius_m'")
    for constraint in ("ck_pending_upload_exif_range", "ck_pending_upload_exif_coords_paired"):
        op.execute(f"ALTER TABLE evidence.pending_upload DROP CONSTRAINT IF EXISTS {constraint}")
    for column in ("exif_captured_at", "exif_longitude", "exif_latitude"):
        op.execute(f"ALTER TABLE evidence.pending_upload DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS core.report_location_detail")
    for name, _ in reversed(ENUMS):
        op.execute(f"DROP TYPE IF EXISTS public.{name}")
