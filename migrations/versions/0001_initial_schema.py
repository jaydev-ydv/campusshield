"""CampusShield initial schema (DATABASE.md Revision 3).

Revision ID: 0001
Revises: None

Creates 8 schemas, 24 enum types, 34 tables, 1 view, 18 trigger functions and
their triggers, and seeds core.system_policy.

Requires PostgreSQL 15+ and NO extensions.  ``gen_random_uuid()`` has been in
core PostgreSQL since 13, so even pgcrypto is unnecessary.

Structural guarantees implemented here rather than left to application code:

* core.report has NO user column.  Attribution lives in identity.report_attribution
  and a trigger refuses to create one for an anonymous report.
* submission_mode and report_kind are immutable after insert, so a report cannot
  be retroactively de-anonymised.
* reporter_contactable is FALSE for every anonymous report, enforced by CHECK, and
  emergency_dispatch refuses to record a contact attempt against such a report.
* risk_assessment.factors rejects reporter_relationship and any credibility-shaped
  key outright, turning the E1 prohibition into a constraint.
* Narrative and evidence retention values are stamped from core.system_policy at
  insert, so amending policy later cannot rewrite the terms applied to past rows.
* Audit tables are append-only and carry no foreign keys — see the note in
  section 6 for why the two facts are connected.
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# 1. Schemas
# ---------------------------------------------------------------------------

SCHEMAS = [
    "CREATE SCHEMA identity",
    "CREATE SCHEMA core",
    "CREATE SCHEMA evidence",
    "CREATE SCHEMA ml",
    "CREATE SCHEMA analytics",
    "CREATE SCHEMA intervention",
    "CREATE SCHEMA notify",
    "CREATE SCHEMA audit",
]

SCHEMA_NAMES = [
    "identity",
    "core",
    "evidence",
    "ml",
    "analytics",
    "intervention",
    "notify",
    "audit",
]


# ---------------------------------------------------------------------------
# 2. Enum types
#
# Created in `public` so every schema can reference them without search_path
# games.  All references below are fully qualified.
# ---------------------------------------------------------------------------

ENUMS: list[tuple[str, list[str]]] = [
    ("user_role", ["student", "security", "icc", "admin"]),
    ("report_kind", ["incident", "concern"]),
    ("submission_mode", ["identified", "anonymous"]),
    ("reporter_relationship", ["affected", "witness", "third_party"]),
    (
        "report_status",
        [
            "submitted",
            "triaged",
            "under_review",
            "action_taken",
            "resolved",
            "closed_no_action",
            "duplicate",
            "withdrawn",
        ],
    ),
    ("risk_band", ["low", "moderate", "high", "critical"]),
    (
        "dispatch_state",
        ["pending", "acknowledged", "dispatched", "on_scene", "stood_down", "closed"],
    ),
    ("link_type", ["duplicate", "related", "same_pattern"]),
    ("link_review", ["unreviewed", "confirmed", "rejected"]),
    ("cluster_status", ["active", "monitoring", "dormant", "resolved"]),
    ("hotspot_status", ["active", "monitoring", "resolved"]),
    ("ml_task", ["classification", "similarity", "clustering", "risk_scoring"]),
    ("model_family", ["rule_based", "baseline", "transformer"]),
    (
        "intervention_type",
        [
            "lighting",
            "patrol",
            "cctv",
            "access_control",
            "signage",
            "awareness",
            "counselling_support",
            "policy",
            "referral",
            "other",
        ],
    ),
    ("intervention_scope", ["report", "cluster", "location", "zone", "campus"]),
    (
        "intervention_status",
        ["proposed", "approved", "in_progress", "completed", "cancelled"],
    ),
    (
        "outcome_status",
        ["effective", "partially_effective", "no_change", "worsened", "inconclusive"],
    ),
    ("notification_audience", ["user", "role", "zone", "all_students"]),
    ("notification_category", ["status_update", "assignment", "alert", "system"]),
    ("delivery_state", ["pending", "sent", "failed", "suppressed"]),
    ("storage_backend", ["firebase_storage", "local"]),
    ("audit_outcome", ["success", "denied", "error"]),
    (
        "policy_origin",
        ["prototype_default", "institutional_requirement", "regulatory"],
    ),
    # Not in DATABASE.md.  Added on explicit instruction so that coordinate
    # verification status is represented in the data model rather than tracked
    # outside it.  See section 3, core.campus_location.
    ("coordinate_status", ["required", "provisional", "verified"]),
]


def _enum_ddl() -> list[str]:
    return [
        "CREATE TYPE public.{} AS ENUM ({})".format(
            name, ", ".join("'{}'".format(v) for v in values)
        )
        for name, values in ENUMS
    ]


# ---------------------------------------------------------------------------
# 3. Tables
#
# Ordered so that every foreign key references an already-created table.  There
# are no circular dependencies: core.report references core.report_cluster, and
# core.report_cluster does not reference core.report — the many-to-many lives in
# core.cluster_member, which is created after both.
# ---------------------------------------------------------------------------

TABLES = [
    # -- identity.app_user ---------------------------------------------------
    """
    CREATE TABLE identity.app_user (
        user_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        firebase_uid        TEXT        NOT NULL,
        role                public.user_role NOT NULL,
        institutional_email TEXT        NOT NULL,
        display_name        TEXT,
        is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        deactivated_at      TIMESTAMPTZ,
        CONSTRAINT ck_app_user_email_shape
            CHECK (position('@' in institutional_email) > 1),
        CONSTRAINT ck_app_user_student_has_no_name
            CHECK (role <> 'student' OR display_name IS NULL)
    )
    """,
    # -- core.system_policy --------------------------------------------------
    """
    CREATE TABLE core.system_policy (
        policy_key   TEXT PRIMARY KEY,
        policy_value TEXT NOT NULL,
        value_type   TEXT NOT NULL,
        origin       public.policy_origin NOT NULL,
        description  TEXT NOT NULL,
        min_value    TEXT,
        max_value    TEXT,
        updated_by   UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_system_policy_description_present
            CHECK (length(description) >= 20),
        CONSTRAINT ck_system_policy_value_type
            CHECK (value_type IN ('integer', 'numeric', 'boolean', 'interval', 'text'))
    )
    """,
    # -- core.campus_zone ----------------------------------------------------
    """
    CREATE TABLE core.campus_zone (
        zone_id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        code             TEXT NOT NULL,
        name             TEXT NOT NULL,
        description      TEXT,
        responsible_role public.user_role,
        CONSTRAINT ck_campus_zone_role CHECK (responsible_role IS NULL OR responsible_role <> 'student')
    )
    """,
    # -- core.campus_location ------------------------------------------------
    #
    # DATABASE.md §7 declares latitude/longitude NOT NULL, arguing that this makes
    # it impossible to seed a placeholder location and forget to fix it.  The
    # instruction for this step is to represent coordinate verification status
    # explicitly instead.  Both are honoured: coordinates are nullable so rows can
    # be staged with coordinate_status = 'required' (CAMPUS_LOCATIONS.md contains
    # 40 such rows and zero verified coordinates), but a location cannot become
    # is_active without verified coordinates AND a recorded source.  The guarantee
    # is unchanged — nothing un-surveyed can enter the working vocabulary — while
    # the survey backlog is now visible in the table rather than in a document.
    """
    CREATE TABLE core.campus_location (
        location_id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        code                   TEXT NOT NULL,
        name                   TEXT NOT NULL,
        zone_id                INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE RESTRICT,
        location_type          TEXT,
        latitude               NUMERIC(9, 6),
        longitude              NUMERIC(9, 6),
        coordinate_status      public.coordinate_status NOT NULL DEFAULT 'required',
        coordinate_source      TEXT,
        coordinate_captured_at TIMESTAMPTZ,
        is_indoor              BOOLEAN,
        has_lighting           BOOLEAN,
        has_cctv               BOOLEAN,
        footfall_band          TEXT,
        dispatch_note          TEXT,
        is_active              BOOLEAN NOT NULL DEFAULT FALSE,
        CONSTRAINT ck_campus_location_latitude
            CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
        CONSTRAINT ck_campus_location_longitude
            CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
        CONSTRAINT ck_campus_location_coords_paired
            CHECK ((latitude IS NULL) = (longitude IS NULL)),
        CONSTRAINT ck_campus_location_required_has_no_coords
            CHECK (coordinate_status <> 'required' OR latitude IS NULL),
        CONSTRAINT ck_campus_location_verified_is_sourced
            CHECK (coordinate_status <> 'verified'
                   OR (latitude IS NOT NULL
                       AND coordinate_source IS NOT NULL
                       AND coordinate_captured_at IS NOT NULL)),
        CONSTRAINT ck_campus_location_active_requires_verified
            CHECK (NOT is_active OR coordinate_status = 'verified'),
        CONSTRAINT ck_campus_location_footfall
            CHECK (footfall_band IS NULL OR footfall_band IN ('high', 'medium', 'low')),
        CONSTRAINT ck_campus_location_type
            CHECK (location_type IS NULL OR location_type IN (
                'academic', 'library', 'laboratory', 'hostel', 'dining', 'sports',
                'sports_support', 'assembly', 'open_assembly', 'recreation',
                'health', 'support', 'amenity', 'admin', 'gate', 'parking',
                'transit', 'circulation', 'path', 'open_ground', 'other'))
    )
    """,
    # -- identity.authority_profile -----------------------------------------
    """
    CREATE TABLE identity.authority_profile (
        user_id                     UUID PRIMARY KEY
                                    REFERENCES identity.app_user (user_id) ON DELETE CASCADE,
        designation                 TEXT NOT NULL,
        department                  TEXT,
        office_phone                TEXT,
        zone_id                     INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE SET NULL,
        can_receive_assignments     BOOLEAN NOT NULL DEFAULT TRUE,
        receives_emergency_dispatch BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    # -- core.report_category ------------------------------------------------
    """
    CREATE TABLE core.report_category (
        category_id              SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        code                     TEXT NOT NULL,
        label                    TEXT NOT NULL,
        kind                     public.report_kind NOT NULL,
        routes_to_role           public.user_role NOT NULL,
        base_severity            SMALLINT NOT NULL,
        requires_confidentiality BOOLEAN NOT NULL DEFAULT FALSE,
        emergency_eligible       BOOLEAN NOT NULL DEFAULT FALSE,
        is_active                BOOLEAN NOT NULL DEFAULT TRUE,
        CONSTRAINT ck_report_category_severity CHECK (base_severity BETWEEN 1 AND 5),
        CONSTRAINT ck_report_category_route    CHECK (routes_to_role <> 'student'),
        CONSTRAINT ck_report_category_emergency_is_incident
            CHECK (NOT emergency_eligible OR kind = 'incident')
    )
    """,
    # -- ml.model_version ----------------------------------------------------
    """
    CREATE TABLE ml.model_version (
        model_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name             TEXT NOT NULL,
        task             public.ml_task NOT NULL,
        family           public.model_family NOT NULL,
        version          TEXT NOT NULL,
        artifact_uri     TEXT,
        embedding_dim    SMALLINT,
        trained_at       TIMESTAMPTZ,
        training_rows    INTEGER,
        hyperparameters  JSONB,
        headline_metrics JSONB,
        is_active        BOOLEAN NOT NULL DEFAULT FALSE,
        CONSTRAINT ck_model_version_dim
            CHECK (embedding_dim IS NULL OR embedding_dim > 0),
        CONSTRAINT ck_model_version_rows
            CHECK (training_rows IS NULL OR training_rows >= 0)
    )
    """,
    # -- core.report_cluster -------------------------------------------------
    """
    CREATE TABLE core.report_cluster (
        cluster_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        label                TEXT,
        primary_location_id  INTEGER REFERENCES core.campus_location (location_id) ON DELETE RESTRICT,
        zone_id              INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE SET NULL,
        dominant_category_id SMALLINT REFERENCES core.report_category (category_id) ON DELETE SET NULL,
        centroid_lat         NUMERIC(9, 6),
        centroid_lng         NUMERIC(9, 6),
        modal_hour_start     SMALLINT,
        modal_hour_end       SMALLINT,
        first_report_at      TIMESTAMPTZ,
        last_report_at       TIMESTAMPTZ,
        report_count         INTEGER NOT NULL DEFAULT 0,
        cluster_risk_score   NUMERIC(5, 2),
        status               public.cluster_status NOT NULL DEFAULT 'active',
        algorithm            TEXT,
        algorithm_params     JSONB,
        detection_run_id     UUID,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_cluster_modal_hour_start
            CHECK (modal_hour_start IS NULL OR modal_hour_start BETWEEN 0 AND 23),
        CONSTRAINT ck_cluster_modal_hour_end
            CHECK (modal_hour_end IS NULL OR modal_hour_end BETWEEN 0 AND 23),
        CONSTRAINT ck_cluster_report_count CHECK (report_count >= 0),
        CONSTRAINT ck_cluster_risk_score
            CHECK (cluster_risk_score IS NULL OR cluster_risk_score BETWEEN 0 AND 100),
        CONSTRAINT ck_cluster_seen_order
            CHECK (first_report_at IS NULL OR last_report_at IS NULL
                   OR first_report_at <= last_report_at)
    )
    """,
    # -- core.report ---------------------------------------------------------
    #
    # No user column.  Not a nullable one, not a hidden one.  Anonymity is the
    # absence of a row in identity.report_attribution, and nothing here can leak
    # an identity because no identity is present.
    """
    CREATE TABLE core.report (
        report_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        public_ref            TEXT NOT NULL,
        report_kind           public.report_kind NOT NULL,
        submission_mode       public.submission_mode NOT NULL,
        reporter_relationship public.reporter_relationship NOT NULL DEFAULT 'affected',
        declared_category_id  SMALLINT REFERENCES core.report_category (category_id) ON DELETE RESTRICT,
        location_id           INTEGER NOT NULL
                              REFERENCES core.campus_location (location_id) ON DELETE RESTRICT,
        location_hint         TEXT,
        occurred_at           TIMESTAMPTZ NOT NULL,
        occurred_hour         SMALLINT NOT NULL,
        occurred_dow          SMALLINT NOT NULL,
        submitted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        is_emergency          BOOLEAN NOT NULL DEFAULT FALSE,
        is_ongoing            BOOLEAN NOT NULL DEFAULT FALSE,
        reporter_contactable  BOOLEAN NOT NULL DEFAULT FALSE,
        current_status        public.report_status NOT NULL DEFAULT 'submitted',
        current_risk_score    NUMERIC(5, 2),
        current_risk_band     public.risk_band,
        current_cluster_id    UUID REFERENCES core.report_cluster (cluster_id) ON DELETE SET NULL,
        source_language       TEXT NOT NULL DEFAULT 'en',
        closed_at             TIMESTAMPTZ,
        CONSTRAINT ck_report_occurred_not_future
            CHECK (occurred_at <= submitted_at),
        CONSTRAINT ck_report_occurred_hour CHECK (occurred_hour BETWEEN 0 AND 23),
        CONSTRAINT ck_report_occurred_dow  CHECK (occurred_dow BETWEEN 0 AND 6),
        -- Anonymous implies not contactable.  FALSE is both the default and the
        -- only legal value for an anonymous report, so any bug fails toward
        -- "we could not reach them" rather than toward exposure.
        CONSTRAINT ck_report_anonymous_is_not_contactable
            CHECK (submission_mode = 'identified' OR reporter_contactable = FALSE),
        CONSTRAINT ck_report_ongoing_requires_emergency
            CHECK (NOT is_ongoing OR is_emergency),
        CONSTRAINT ck_report_risk_score
            CHECK (current_risk_score IS NULL OR current_risk_score BETWEEN 0 AND 100),
        CONSTRAINT ck_report_public_ref_shape
            CHECK (public_ref ~ '^CS-[0-9]{4}-[A-Z0-9]{6}$')
    )
    """,
    # -- core.report_narrative ----------------------------------------------
    """
    CREATE TABLE core.report_narrative (
        report_id                        UUID PRIMARY KEY
                                         REFERENCES core.report (report_id) ON DELETE CASCADE,
        narrative                        TEXT,
        narrative_redacted               TEXT,
        redaction_state                  TEXT NOT NULL DEFAULT 'pending',
        redacted_at                      TIMESTAMPTZ,
        word_count                       INTEGER,
        retention_policy_key             TEXT NOT NULL
                                         REFERENCES core.system_policy (policy_key) ON DELETE RESTRICT,
        narrative_retention_days_applied INTEGER NOT NULL,
        redacted_retention_days_applied  INTEGER NOT NULL,
        narrative_expires_at             TIMESTAMPTZ,
        redacted_expires_at              TIMESTAMPTZ,
        narrative_purged_at              TIMESTAMPTZ,
        redacted_purged_at               TIMESTAMPTZ,
        purge_reason                     TEXT,
        CONSTRAINT ck_narrative_length
            CHECK (narrative IS NULL OR length(narrative) BETWEEN 10 AND 8000),
        -- A NULL narrative must be an explained NULL, never an accident.
        CONSTRAINT ck_narrative_purge_pairing
            CHECK ((narrative IS NULL) = (narrative_purged_at IS NOT NULL)),
        -- One-directional for the redacted copy: it is legitimately NULL before
        -- redaction runs, so only the purged direction can be asserted.
        CONSTRAINT ck_redacted_purge_pairing
            CHECK (redacted_purged_at IS NULL OR narrative_redacted IS NULL),
        CONSTRAINT ck_narrative_retention_days_positive
            CHECK (narrative_retention_days_applied > 0
                   AND redacted_retention_days_applied > 0),
        CONSTRAINT ck_narrative_redaction_state
            CHECK (redaction_state IN ('pending', 'auto_redacted', 'human_reviewed')),
        CONSTRAINT ck_narrative_purge_reason
            CHECK (purge_reason IS NULL
                   OR purge_reason IN ('retention_expiry', 'withdrawn', 'admin_request')),
        CONSTRAINT ck_narrative_word_count
            CHECK (word_count IS NULL OR word_count >= 0)
    )
    """,
    # -- identity.report_attribution ----------------------------------------
    #
    # The only place a report is ever connected to a person.  Primary key on
    # report_id enforces at most one attribution; the trigger in section 5
    # enforces that anonymous reports get none.
    """
    CREATE TABLE identity.report_attribution (
        report_id           UUID PRIMARY KEY
                            REFERENCES core.report (report_id) ON DELETE CASCADE,
        user_id             UUID NOT NULL
                            REFERENCES identity.app_user (user_id) ON DELETE RESTRICT,
        attributed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        contact_consent     BOOLEAN NOT NULL DEFAULT TRUE,
        callback_contact    TEXT,
        callback_expires_at TIMESTAMPTZ,
        CONSTRAINT ck_attribution_callback_pairing
            CHECK ((callback_contact IS NULL) = (callback_expires_at IS NULL)),
        CONSTRAINT ck_attribution_callback_requires_consent
            CHECK (callback_contact IS NULL OR contact_consent)
    )
    """,
    # -- identity.submission_quota ------------------------------------------
    #
    # A counter, not a link.  Stores how many reports a user filed today, never
    # which ones.  The rate limit works; the deanonymisation does not.
    """
    CREATE TABLE identity.submission_quota (
        user_id         UUID NOT NULL
                        REFERENCES identity.app_user (user_id) ON DELETE CASCADE,
        quota_date      DATE NOT NULL,
        submitted_count SMALLINT NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, quota_date),
        CONSTRAINT ck_submission_quota_count CHECK (submitted_count >= 0)
    )
    """,
    # -- core.report_access_token -------------------------------------------
    """
    CREATE TABLE core.report_access_token (
        token_hash   TEXT PRIMARY KEY,
        report_id    UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ,
        use_count    INTEGER NOT NULL DEFAULT 0,
        revoked_at   TIMESTAMPTZ,
        CONSTRAINT ck_report_access_token_is_sha256
            CHECK (token_hash ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_report_access_token_use_count CHECK (use_count >= 0),
        CONSTRAINT ck_report_access_token_expiry
            CHECK (expires_at IS NULL OR expires_at > issued_at)
    )
    """,
    # -- core.emergency_dispatch --------------------------------------------
    """
    CREATE TABLE core.emergency_dispatch (
        dispatch_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        report_id         UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        state             public.dispatch_state NOT NULL DEFAULT 'pending',
        raised_at         TIMESTAMPTZ NOT NULL,
        acknowledged_at   TIMESTAMPTZ,
        acknowledged_by   UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        dispatched_at     TIMESTAMPTZ,
        on_scene_at       TIMESTAMPTZ,
        closed_at         TIMESTAMPTZ,
        responder_note    TEXT,
        public_note       TEXT,
        contact_attempted BOOLEAN NOT NULL DEFAULT FALSE,
        outcome_summary   TEXT,
        CONSTRAINT ck_dispatch_ack_after_raise
            CHECK (acknowledged_at IS NULL OR acknowledged_at >= raised_at),
        CONSTRAINT ck_dispatch_dispatched_after_ack
            CHECK (dispatched_at IS NULL OR acknowledged_at IS NULL
                   OR dispatched_at >= acknowledged_at),
        CONSTRAINT ck_dispatch_scene_after_dispatch
            CHECK (on_scene_at IS NULL OR dispatched_at IS NULL
                   OR on_scene_at >= dispatched_at),
        CONSTRAINT ck_dispatch_closed_after_raise
            CHECK (closed_at IS NULL OR closed_at >= raised_at)
    )
    """,
    # -- core.cluster_member -------------------------------------------------
    """
    CREATE TABLE core.cluster_member (
        cluster_id       UUID NOT NULL
                         REFERENCES core.report_cluster (cluster_id) ON DELETE CASCADE,
        report_id        UUID NOT NULL
                         REFERENCES core.report (report_id) ON DELETE CASCADE,
        membership_score NUMERIC(5, 4),
        added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        detection_run_id UUID,
        PRIMARY KEY (cluster_id, report_id),
        CONSTRAINT ck_cluster_member_score
            CHECK (membership_score IS NULL OR membership_score BETWEEN 0 AND 1)
    )
    """,
    # -- core.report_link ----------------------------------------------------
    #
    # Canonical ordering (a < b) is what makes the UNIQUE constraint actually
    # prevent duplicates.  Without it (A,B) and (B,A) both insert and the
    # deduplication silently stops working.
    """
    CREATE TABLE core.report_link (
        link_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id_a  UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        report_id_b  UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        link_type    public.link_type NOT NULL,
        similarity   NUMERIC(5, 4),
        method       TEXT,
        model_id     UUID REFERENCES ml.model_version (model_id) ON DELETE SET NULL,
        detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        review_state public.link_review NOT NULL DEFAULT 'unreviewed',
        reviewed_by  UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        CONSTRAINT ck_report_link_canonical_order CHECK (report_id_a < report_id_b),
        CONSTRAINT ck_report_link_distinct        CHECK (report_id_a <> report_id_b),
        CONSTRAINT ck_report_link_similarity
            CHECK (similarity IS NULL OR similarity BETWEEN 0 AND 1),
        CONSTRAINT ck_report_link_method
            CHECK (method IS NULL OR method IN
                   ('embedding_cosine', 'location_time_proximity', 'manual')),
        CONSTRAINT ck_report_link_reviewed
            CHECK (review_state = 'unreviewed' OR reviewed_by IS NOT NULL)
    )
    """,
    # -- core.risk_assessment ------------------------------------------------
    #
    # ck_risk_factors_no_credibility_terms turns the E1 prohibition from a
    # documented convention into a constraint.  A scorer that discounts witness
    # reports has built a credibility model whatever it is called in the code;
    # this makes storing the result impossible.
    """
    CREATE TABLE core.risk_assessment (
        assessment_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id      UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        score          NUMERIC(5, 2) NOT NULL,
        band           public.risk_band NOT NULL,
        scorer_version TEXT NOT NULL,
        model_id       UUID REFERENCES ml.model_version (model_id) ON DELETE SET NULL,
        factors        JSONB NOT NULL,
        computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        is_current     BOOLEAN NOT NULL DEFAULT TRUE,
        trigger_reason TEXT,
        CONSTRAINT ck_risk_score_range CHECK (score BETWEEN 0 AND 100),
        CONSTRAINT ck_risk_factors_is_object CHECK (jsonb_typeof(factors) = 'object'),
        CONSTRAINT ck_risk_factors_no_credibility_terms CHECK (
            NOT (factors ?| ARRAY['reporter_relationship', 'credibility',
                                  'credibility_score', 'trust', 'trust_score',
                                  'reliability', 'reporter_score'])
            AND NOT (COALESCE(factors -> 'weights', '{}'::jsonb) ?|
                     ARRAY['reporter_relationship', 'credibility',
                           'credibility_score', 'trust', 'trust_score',
                           'reliability', 'reporter_score'])
        ),
        CONSTRAINT ck_risk_trigger_reason
            CHECK (trigger_reason IS NULL OR trigger_reason IN
                   ('initial', 'new_related_report', 'cluster_growth', 'manual_review'))
    )
    """,
    # -- core.case_assignment ------------------------------------------------
    """
    CREATE TABLE core.case_assignment (
        assignment_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id       UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        assigned_to     UUID NOT NULL REFERENCES identity.app_user (user_id) ON DELETE RESTRICT,
        assigned_role   public.user_role NOT NULL,
        assigned_by     UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        released_at     TIMESTAMPTZ,
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        assignment_note TEXT,
        CONSTRAINT ck_case_assignment_role_not_student CHECK (assigned_role <> 'student'),
        CONSTRAINT ck_case_assignment_active_not_released
            CHECK (NOT is_active OR released_at IS NULL),
        CONSTRAINT ck_case_assignment_release_order
            CHECK (released_at IS NULL OR released_at >= assigned_at)
    )
    """,
    # -- core.case_status_history -------------------------------------------
    """
    CREATE TABLE core.case_status_history (
        history_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id           UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        from_status         public.report_status,
        to_status           public.report_status NOT NULL,
        changed_by          UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        changed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        remark              TEXT,
        visible_to_reporter BOOLEAN NOT NULL DEFAULT TRUE,
        CONSTRAINT ck_case_status_actually_changed
            CHECK (from_status IS DISTINCT FROM to_status)
    )
    """,
    # -- evidence.evidence_object -------------------------------------------
    #
    # No uploader column.  An uploader FK would silently de-anonymise every
    # anonymous report that carried a photo.
    """
    CREATE TABLE evidence.evidence_object (
        evidence_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        report_id              UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        storage_backend        public.storage_backend NOT NULL,
        storage_path           TEXT NOT NULL,
        content_type           TEXT NOT NULL,
        byte_size              BIGINT NOT NULL,
        sha256                 TEXT,
        original_filename      TEXT,
        uploaded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        retention_policy_key   TEXT NOT NULL
                               REFERENCES core.system_policy (policy_key) ON DELETE RESTRICT,
        retention_days_applied INTEGER NOT NULL,
        retention_expires_at   TIMESTAMPTZ,
        is_purged              BOOLEAN NOT NULL DEFAULT FALSE,
        purged_at              TIMESTAMPTZ,
        purge_reason           TEXT,
        CONSTRAINT ck_evidence_byte_size CHECK (byte_size > 0 AND byte_size <= 26214400),
        CONSTRAINT ck_evidence_purged_pairing CHECK (NOT is_purged OR purged_at IS NOT NULL),
        CONSTRAINT ck_evidence_retention_days CHECK (retention_days_applied > 0),
        CONSTRAINT ck_evidence_sha256 CHECK (sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_evidence_purge_reason
            CHECK (purge_reason IS NULL
                   OR purge_reason IN ('retention_expiry', 'withdrawn', 'admin_request'))
    )
    """,
    # -- ml.report_classification -------------------------------------------
    """
    CREATE TABLE ml.report_classification (
        classification_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id              UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        model_id               UUID NOT NULL REFERENCES ml.model_version (model_id) ON DELETE RESTRICT,
        predicted_category_id  SMALLINT REFERENCES core.report_category (category_id) ON DELETE RESTRICT,
        confidence             NUMERIC(5, 4),
        label_scores           JSONB,
        inferred_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        latency_ms             INTEGER,
        is_current             BOOLEAN NOT NULL DEFAULT FALSE,
        overridden_by          UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        overridden_category_id SMALLINT REFERENCES core.report_category (category_id) ON DELETE RESTRICT,
        overridden_at          TIMESTAMPTZ,
        CONSTRAINT ck_classification_confidence
            CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
        CONSTRAINT ck_classification_override_pairing
            CHECK ((overridden_by IS NULL) = (overridden_category_id IS NULL)),
        CONSTRAINT ck_classification_override_timestamp
            CHECK ((overridden_category_id IS NULL) = (overridden_at IS NULL)),
        CONSTRAINT ck_classification_latency
            CHECK (latency_ms IS NULL OR latency_ms >= 0)
    )
    """,
    # -- ml.report_embedding -------------------------------------------------
    #
    # REAL[] not vector(N): no pgvector.  l2_norm is precomputed so cosine
    # similarity is a dot product over two stored norms.
    """
    CREATE TABLE ml.report_embedding (
        report_id   UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        model_id    UUID NOT NULL REFERENCES ml.model_version (model_id) ON DELETE CASCADE,
        embedding   REAL[] NOT NULL,
        dim         SMALLINT NOT NULL,
        l2_norm     REAL NOT NULL,
        computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (report_id, model_id),
        CONSTRAINT ck_embedding_dim_matches CHECK (dim = array_length(embedding, 1)),
        CONSTRAINT ck_embedding_norm_positive CHECK (l2_norm > 0)
    )
    """,
    # -- ml.annotation -------------------------------------------------------
    """
    CREATE TABLE ml.annotation (
        annotation_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source           TEXT NOT NULL,
        external_ref     TEXT,
        report_id        UUID REFERENCES core.report (report_id) ON DELETE CASCADE,
        text_redacted    TEXT,
        gold_category_id SMALLINT REFERENCES core.report_category (category_id) ON DELETE RESTRICT,
        split            TEXT,
        annotated_by     UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        annotated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_annotation_source
            CHECK (source IN ('external_corpus', 'authority_override',
                              'manual_seed', 'synthetic')),
        CONSTRAINT ck_annotation_split
            CHECK (split IS NULL OR split IN ('train', 'val', 'test')),
        CONSTRAINT ck_annotation_has_content
            CHECK (report_id IS NOT NULL OR text_redacted IS NOT NULL)
    )
    """,
    # -- ml.evaluation_run ---------------------------------------------------
    """
    CREATE TABLE ml.evaluation_run (
        run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        model_id          UUID NOT NULL REFERENCES ml.model_version (model_id) ON DELETE CASCADE,
        dataset_label     TEXT NOT NULL,
        split             TEXT,
        n_samples         INTEGER,
        accuracy          NUMERIC(5, 4),
        macro_f1          NUMERIC(5, 4),
        weighted_f1       NUMERIC(5, 4),
        precision_macro   NUMERIC(5, 4),
        recall_macro      NUMERIC(5, 4),
        per_class_metrics JSONB,
        confusion_matrix  JSONB,
        mean_latency_ms   INTEGER,
        run_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        notes             TEXT,
        CONSTRAINT ck_eval_n_samples CHECK (n_samples IS NULL OR n_samples > 0),
        CONSTRAINT ck_eval_split
            CHECK (split IS NULL OR split IN ('train', 'val', 'test')),
        CONSTRAINT ck_eval_metric_ranges CHECK (
            (accuracy        IS NULL OR accuracy        BETWEEN 0 AND 1) AND
            (macro_f1        IS NULL OR macro_f1        BETWEEN 0 AND 1) AND
            (weighted_f1     IS NULL OR weighted_f1     BETWEEN 0 AND 1) AND
            (precision_macro IS NULL OR precision_macro BETWEEN 0 AND 1) AND
            (recall_macro    IS NULL OR recall_macro    BETWEEN 0 AND 1)
        )
    )
    """,
    # -- analytics.hotspot ---------------------------------------------------
    """
    CREATE TABLE analytics.hotspot (
        hotspot_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        location_id         INTEGER NOT NULL
                            REFERENCES core.campus_location (location_id) ON DELETE RESTRICT,
        zone_id             INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE SET NULL,
        cluster_id          UUID REFERENCES core.report_cluster (cluster_id) ON DELETE SET NULL,
        window_start        TIMESTAMPTZ NOT NULL,
        window_end          TIMESTAMPTZ NOT NULL,
        report_count        INTEGER NOT NULL,
        density_score       NUMERIC(6, 3),
        dominant_hour_band  TEXT,
        severity_band       public.risk_band,
        status              public.hotspot_status NOT NULL DEFAULT 'active',
        detection_run_id    UUID,
        detection_algorithm TEXT,
        detection_params    JSONB,
        detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        resolved_at         TIMESTAMPTZ,
        CONSTRAINT ck_hotspot_window CHECK (window_start < window_end),
        CONSTRAINT ck_hotspot_report_count CHECK (report_count >= 0),
        CONSTRAINT ck_hotspot_resolved_pairing
            CHECK (status <> 'resolved' OR resolved_at IS NOT NULL),
        CONSTRAINT ck_hotspot_algorithm
            CHECK (detection_algorithm IS NULL
                   OR detection_algorithm IN ('dbscan', 'grid_density', 'manual'))
    )
    """,
    # -- intervention.intervention ------------------------------------------
    #
    # report_id is not listed in DATABASE.md §15 but intervention_scope includes
    # 'report'.  Without this column the scope constraint would be unsatisfiable
    # for that value.  Added so the enum and the table agree.
    """
    CREATE TABLE intervention.intervention (
        intervention_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title             TEXT NOT NULL,
        description       TEXT,
        intervention_type public.intervention_type NOT NULL,
        scope             public.intervention_scope NOT NULL,
        location_id       INTEGER REFERENCES core.campus_location (location_id) ON DELETE RESTRICT,
        zone_id           INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE RESTRICT,
        cluster_id        UUID REFERENCES core.report_cluster (cluster_id) ON DELETE RESTRICT,
        hotspot_id        UUID REFERENCES analytics.hotspot (hotspot_id) ON DELETE RESTRICT,
        report_id         UUID REFERENCES core.report (report_id) ON DELETE RESTRICT,
        status            public.intervention_status NOT NULL DEFAULT 'proposed',
        proposed_by       UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        approved_by       UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        proposed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        approved_at       TIMESTAMPTZ,
        started_at        TIMESTAMPTZ,
        completed_at      TIMESTAMPTZ,
        expected_effect   TEXT,
        CONSTRAINT ck_intervention_scope_target CHECK (
            (scope = 'report'   AND report_id   IS NOT NULL) OR
            (scope = 'cluster'  AND cluster_id  IS NOT NULL) OR
            (scope = 'location' AND location_id IS NOT NULL) OR
            (scope = 'zone'     AND zone_id     IS NOT NULL) OR
            (scope = 'campus')
        ),
        CONSTRAINT ck_intervention_date_order
            CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at),
        CONSTRAINT ck_intervention_completed_has_date
            CHECK (status <> 'completed' OR completed_at IS NOT NULL),
        CONSTRAINT ck_intervention_approved_pairing
            CHECK ((approved_by IS NULL) = (approved_at IS NULL))
    )
    """,
    # -- intervention.intervention_report_link ------------------------------
    """
    CREATE TABLE intervention.intervention_report_link (
        intervention_id UUID NOT NULL
                        REFERENCES intervention.intervention (intervention_id) ON DELETE CASCADE,
        report_id       UUID NOT NULL REFERENCES core.report (report_id) ON DELETE CASCADE,
        linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        link_note       TEXT,
        PRIMARY KEY (intervention_id, report_id)
    )
    """,
    # -- intervention.impact_measurement ------------------------------------
    #
    # A difference-in-differences result cannot be stored without the differences
    # it claims to have taken.  A drop in reports at an intervention site is
    # ambiguous on its own: the area may have got safer, or students may have
    # stopped trusting the system.  The control columns are what separate those.
    """
    CREATE TABLE intervention.impact_measurement (
        measurement_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        intervention_id          UUID NOT NULL
                                 REFERENCES intervention.intervention (intervention_id) ON DELETE CASCADE,
        location_id              INTEGER REFERENCES core.campus_location (location_id) ON DELETE RESTRICT,
        zone_id                  INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE SET NULL,
        cluster_id               UUID REFERENCES core.report_cluster (cluster_id) ON DELETE SET NULL,
        category_filter_id       SMALLINT REFERENCES core.report_category (category_id) ON DELETE RESTRICT,
        baseline_start           TIMESTAMPTZ NOT NULL,
        baseline_end             TIMESTAMPTZ NOT NULL,
        followup_start           TIMESTAMPTZ NOT NULL,
        followup_end             TIMESTAMPTZ NOT NULL,
        window_days              INTEGER NOT NULL,
        baseline_count           INTEGER NOT NULL,
        followup_count           INTEGER NOT NULL,
        baseline_rate_per_week   NUMERIC(8, 3),
        followup_rate_per_week   NUMERIC(8, 3),
        absolute_change          INTEGER,
        percent_change           NUMERIC(7, 2),
        control_location_ids     INTEGER[],
        control_selection_method TEXT,
        control_baseline_count   INTEGER,
        control_followup_count   INTEGER,
        control_percent_change   NUMERIC(7, 2),
        net_percent_change       NUMERIC(7, 2),
        method                   TEXT NOT NULL DEFAULT 'simple_before_after',
        is_significant           BOOLEAN,
        significance_note        TEXT,
        computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_impact_baseline_window CHECK (baseline_start < baseline_end),
        CONSTRAINT ck_impact_followup_window CHECK (followup_start < followup_end),
        CONSTRAINT ck_impact_window_order    CHECK (baseline_end <= followup_start),
        CONSTRAINT ck_impact_window_days     CHECK (window_days > 0),
        CONSTRAINT ck_impact_counts_non_negative CHECK (
            baseline_count >= 0 AND followup_count >= 0
            AND (control_baseline_count IS NULL OR control_baseline_count >= 0)
            AND (control_followup_count IS NULL OR control_followup_count >= 0)
        ),
        CONSTRAINT ck_impact_controls_non_empty
            CHECK (control_location_ids IS NULL
                   OR array_length(control_location_ids, 1) >= 1),
        CONSTRAINT ck_impact_method
            CHECK (method IN ('simple_before_after', 'difference_in_differences')),
        CONSTRAINT ck_impact_control_selection_method
            CHECK (control_selection_method IS NULL
                   OR control_selection_method IN
                      ('same_zone', 'same_location_type', 'manual', 'campus_wide')),
        CONSTRAINT ck_impact_did_requires_controls CHECK (
            method <> 'difference_in_differences'
            OR (control_location_ids IS NOT NULL AND net_percent_change IS NOT NULL)
        ),
        CONSTRAINT ck_impact_controls_have_method
            CHECK (control_location_ids IS NULL OR control_selection_method IS NOT NULL)
    )
    """,
    # -- intervention.intervention_outcome ----------------------------------
    """
    CREATE TABLE intervention.intervention_outcome (
        outcome_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        intervention_id           UUID NOT NULL
                                  REFERENCES intervention.intervention (intervention_id) ON DELETE CASCADE,
        measurement_id            UUID REFERENCES intervention.impact_measurement (measurement_id) ON DELETE SET NULL,
        outcome_status            public.outcome_status NOT NULL,
        assessed_by               UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        assessed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
        narrative                 TEXT,
        follow_up_required        BOOLEAN NOT NULL DEFAULT FALSE,
        follow_up_intervention_id UUID REFERENCES intervention.intervention (intervention_id) ON DELETE SET NULL,
        CONSTRAINT ck_outcome_no_self_follow_up
            CHECK (follow_up_intervention_id IS NULL
                   OR follow_up_intervention_id <> intervention_id)
    )
    """,
    # -- notify.broadcast_alert ---------------------------------------------
    """
    CREATE TABLE notify.broadcast_alert (
        alert_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title              TEXT NOT NULL,
        body               TEXT NOT NULL,
        severity           public.risk_band NOT NULL,
        zone_id            INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE SET NULL,
        location_id        INTEGER REFERENCES core.campus_location (location_id) ON DELETE SET NULL,
        related_hotspot_id UUID REFERENCES analytics.hotspot (hotspot_id) ON DELETE SET NULL,
        issued_by          UUID REFERENCES identity.app_user (user_id) ON DELETE SET NULL,
        issued_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at         TIMESTAMPTZ,
        is_active          BOOLEAN NOT NULL DEFAULT TRUE,
        CONSTRAINT ck_alert_expiry CHECK (expires_at IS NULL OR expires_at > issued_at)
    )
    """,
    # -- notify.notification -------------------------------------------------
    """
    CREATE TABLE notify.notification (
        notification_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        audience           public.notification_audience NOT NULL,
        recipient_user_id  UUID REFERENCES identity.app_user (user_id) ON DELETE CASCADE,
        recipient_role     public.user_role,
        recipient_zone_id  INTEGER REFERENCES core.campus_zone (zone_id) ON DELETE CASCADE,
        category           public.notification_category NOT NULL,
        title              TEXT NOT NULL,
        body               TEXT NOT NULL,
        related_report_id  UUID REFERENCES core.report (report_id) ON DELETE CASCADE,
        related_hotspot_id UUID REFERENCES analytics.hotspot (hotspot_id) ON DELETE SET NULL,
        related_alert_id   UUID REFERENCES notify.broadcast_alert (alert_id) ON DELETE CASCADE,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivery_state     public.delivery_state NOT NULL DEFAULT 'pending',
        sent_at            TIMESTAMPTZ,
        read_at            TIMESTAMPTZ,
        fcm_message_id     TEXT,
        CONSTRAINT ck_notification_audience_target CHECK (
            (audience = 'user' AND recipient_user_id IS NOT NULL) OR
            (audience = 'role' AND recipient_role    IS NOT NULL) OR
            (audience = 'zone' AND recipient_zone_id IS NOT NULL) OR
            (audience = 'all_students')
        ),
        CONSTRAINT ck_notification_sent_pairing
            CHECK (delivery_state <> 'sent' OR sent_at IS NOT NULL)
    )
    """,
    # -- notify.device_token -------------------------------------------------
    #
    # Bound to a user, never to a report.  There is deliberately no report_id
    # column: adding one would create the identity link that the absence of
    # identity.report_attribution is supposed to guarantee.
    """
    CREATE TABLE notify.device_token (
        device_token_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id         UUID NOT NULL REFERENCES identity.app_user (user_id) ON DELETE CASCADE,
        fcm_token       TEXT NOT NULL,
        platform        TEXT,
        registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen_at    TIMESTAMPTZ,
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        CONSTRAINT ck_device_token_platform
            CHECK (platform IS NULL OR platform IN ('web', 'android', 'ios'))
    )
    """,
    # -- audit.access_log ----------------------------------------------------
    #
    # NO FOREIGN KEYS, deliberately.  An FK to identity.app_user would need an
    # ON DELETE action, and every available action conflicts with append-only:
    # CASCADE would delete audit rows, SET NULL would UPDATE them, and both are
    # blocked by the append-only trigger, which would in turn make deleting a
    # user impossible.  Audit records outlive the rows they describe, so actor
    # and object identifiers are stored as bare values.
    """
    CREATE TABLE audit.access_log (
        log_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        actor_user_id   UUID,
        actor_role      public.user_role,
        action          TEXT NOT NULL,
        object_type     TEXT NOT NULL,
        object_id       TEXT NOT NULL,
        outcome         public.audit_outcome NOT NULL,
        occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        ip_hash         TEXT,
        user_agent_hash TEXT,
        request_id      UUID,
        detail          JSONB,
        CONSTRAINT ck_access_log_detail_is_object
            CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object'),
        CONSTRAINT ck_access_log_action_present CHECK (length(action) > 0)
    )
    """,
    # -- audit.identity_disclosure_log --------------------------------------
    """
    CREATE TABLE audit.identity_disclosure_log (
        disclosure_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id     UUID NOT NULL,
        disclosed_to  UUID NOT NULL,
        disclosed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        purpose       TEXT NOT NULL,
        legal_basis   TEXT,
        approved_by   UUID,
        CONSTRAINT ck_disclosure_purpose_written CHECK (length(purpose) >= 10)
    )
    """,
]


# ---------------------------------------------------------------------------
# 4. Indexes
#
# Partial unique indexes replace triggers wherever possible: cheaper, race-free
# under concurrency, and impossible to forget to fire.
# ---------------------------------------------------------------------------

INDEXES = [
    # identity.app_user
    "CREATE UNIQUE INDEX uq_app_user_firebase_uid ON identity.app_user (firebase_uid)",
    "CREATE UNIQUE INDEX uq_app_user_email_lower ON identity.app_user (lower(institutional_email))",
    "CREATE INDEX ix_app_user_role_active ON identity.app_user (role) WHERE is_active",
    # identity.authority_profile
    "CREATE INDEX ix_authority_profile_zone ON identity.authority_profile (zone_id)",
    "CREATE INDEX ix_authority_profile_assignable ON identity.authority_profile (user_id) WHERE can_receive_assignments",
    "CREATE INDEX ix_authority_profile_dispatch ON identity.authority_profile (user_id) WHERE receives_emergency_dispatch",
    # identity.report_attribution
    "CREATE INDEX ix_report_attribution_user ON identity.report_attribution (user_id)",
    "CREATE INDEX ix_report_attribution_callback_expiry ON identity.report_attribution (callback_expires_at) WHERE callback_contact IS NOT NULL",
    # core.system_policy
    "CREATE INDEX ix_system_policy_origin ON core.system_policy (origin)",
    # core.campus_zone / campus_location
    "CREATE UNIQUE INDEX uq_campus_zone_code ON core.campus_zone (code)",
    "CREATE UNIQUE INDEX uq_campus_location_code ON core.campus_location (code)",
    "CREATE INDEX ix_campus_location_zone ON core.campus_location (zone_id)",
    "CREATE INDEX ix_campus_location_type ON core.campus_location (location_type)",
    "CREATE INDEX ix_campus_location_active ON core.campus_location (is_active)",
    "CREATE INDEX ix_campus_location_survey_backlog ON core.campus_location (coordinate_status) WHERE coordinate_status <> 'verified'",
    # core.report_category
    "CREATE UNIQUE INDEX uq_report_category_code ON core.report_category (code)",
    "CREATE INDEX ix_report_category_kind_active ON core.report_category (kind, is_active)",
    "CREATE INDEX ix_report_category_route ON core.report_category (routes_to_role)",
    # core.report
    "CREATE UNIQUE INDEX uq_report_public_ref ON core.report (public_ref)",
    "CREATE INDEX ix_report_location_occurred ON core.report (location_id, occurred_at DESC)",
    "CREATE INDEX ix_report_triage_queue ON core.report (current_status, current_risk_score DESC)",
    "CREATE INDEX ix_report_submitted_at ON core.report (submitted_at DESC)",
    "CREATE INDEX ix_report_cluster ON core.report (current_cluster_id) WHERE current_cluster_id IS NOT NULL",
    "CREATE INDEX ix_report_occurred_hour ON core.report (occurred_hour)",
    "CREATE INDEX ix_report_location_submitted ON core.report (location_id, submitted_at)",
    "CREATE INDEX ix_report_emergency_open ON core.report (submitted_at DESC) WHERE is_emergency AND current_status = 'submitted'",
    "CREATE INDEX ix_report_category ON core.report (declared_category_id)",
    # core.report_narrative
    "CREATE INDEX ix_narrative_fts ON core.report_narrative USING GIN (to_tsvector('english', COALESCE(narrative_redacted, '')))",
    "CREATE INDEX ix_narrative_expiry ON core.report_narrative (narrative_expires_at) WHERE narrative_purged_at IS NULL",
    "CREATE INDEX ix_narrative_redacted_expiry ON core.report_narrative (redacted_expires_at) WHERE redacted_purged_at IS NULL",
    # core.report_access_token
    "CREATE UNIQUE INDEX uq_report_access_token_report ON core.report_access_token (report_id)",
    "CREATE INDEX ix_report_access_token_expiry ON core.report_access_token (expires_at) WHERE revoked_at IS NULL",
    # core.emergency_dispatch
    "CREATE UNIQUE INDEX uq_emergency_dispatch_report ON core.emergency_dispatch (report_id)",
    "CREATE INDEX ix_emergency_dispatch_open ON core.emergency_dispatch (state) WHERE state <> 'closed'",
    "CREATE INDEX ix_emergency_dispatch_raised ON core.emergency_dispatch (raised_at DESC)",
    # core.report_link
    "CREATE UNIQUE INDEX uq_report_link_pair ON core.report_link (report_id_a, report_id_b, link_type)",
    "CREATE INDEX ix_report_link_a ON core.report_link (report_id_a)",
    "CREATE INDEX ix_report_link_b ON core.report_link (report_id_b)",
    "CREATE INDEX ix_report_link_unreviewed ON core.report_link (review_state) WHERE review_state = 'unreviewed'",
    # core.report_cluster / cluster_member
    "CREATE INDEX ix_cluster_primary_location ON core.report_cluster (primary_location_id)",
    "CREATE INDEX ix_cluster_status_risk ON core.report_cluster (status, cluster_risk_score DESC)",
    "CREATE INDEX ix_cluster_last_report ON core.report_cluster (last_report_at DESC)",
    "CREATE INDEX ix_cluster_detection_run ON core.report_cluster (detection_run_id)",
    "CREATE INDEX ix_cluster_member_report ON core.cluster_member (report_id)",
    # core.risk_assessment
    "CREATE UNIQUE INDEX uq_risk_assessment_current ON core.risk_assessment (report_id) WHERE is_current",
    "CREATE INDEX ix_risk_assessment_history ON core.risk_assessment (report_id, computed_at DESC)",
    "CREATE INDEX ix_risk_assessment_band ON core.risk_assessment (band, computed_at DESC)",
    # core.case_assignment / case_status_history
    "CREATE UNIQUE INDEX uq_case_assignment_active ON core.case_assignment (report_id) WHERE is_active",
    "CREATE INDEX ix_case_assignment_my_queue ON core.case_assignment (assigned_to) WHERE is_active",
    "CREATE INDEX ix_case_assignment_history ON core.case_assignment (report_id, assigned_at DESC)",
    "CREATE INDEX ix_case_status_history_report ON core.case_status_history (report_id, changed_at DESC)",
    "CREATE INDEX ix_case_status_history_status ON core.case_status_history (to_status, changed_at DESC)",
    "CREATE INDEX ix_case_status_history_changed ON core.case_status_history (changed_at DESC)",
    # evidence.evidence_object
    "CREATE INDEX ix_evidence_report ON evidence.evidence_object (report_id)",
    "CREATE INDEX ix_evidence_sha256 ON evidence.evidence_object (sha256)",
    "CREATE INDEX ix_evidence_retention ON evidence.evidence_object (retention_expires_at) WHERE NOT is_purged",
    "CREATE INDEX ix_evidence_purged ON evidence.evidence_object (is_purged)",
    # ml
    "CREATE UNIQUE INDEX uq_model_version_name_version ON ml.model_version (name, version)",
    "CREATE UNIQUE INDEX uq_model_version_one_active_per_task ON ml.model_version (task) WHERE is_active",
    "CREATE UNIQUE INDEX uq_classification_current ON ml.report_classification (report_id) WHERE is_current",
    "CREATE INDEX ix_classification_report_model ON ml.report_classification (report_id, model_id)",
    "CREATE INDEX ix_classification_model_time ON ml.report_classification (model_id, inferred_at DESC)",
    "CREATE INDEX ix_classification_predicted ON ml.report_classification (predicted_category_id)",
    "CREATE INDEX ix_classification_overrides ON ml.report_classification (overridden_at) WHERE overridden_by IS NOT NULL",
    "CREATE INDEX ix_embedding_model ON ml.report_embedding (model_id)",
    "CREATE INDEX ix_embedding_computed ON ml.report_embedding (computed_at DESC)",
    "CREATE INDEX ix_annotation_split ON ml.annotation (split)",
    "CREATE INDEX ix_annotation_gold ON ml.annotation (gold_category_id)",
    "CREATE INDEX ix_annotation_source ON ml.annotation (source)",
    "CREATE UNIQUE INDEX uq_annotation_source_ref ON ml.annotation (source, external_ref) WHERE external_ref IS NOT NULL",
    "CREATE INDEX ix_eval_model_time ON ml.evaluation_run (model_id, run_at DESC)",
    "CREATE INDEX ix_eval_dataset ON ml.evaluation_run (dataset_label, split)",
    # analytics.hotspot
    "CREATE INDEX ix_hotspot_location_window ON analytics.hotspot (location_id, window_end DESC)",
    "CREATE INDEX ix_hotspot_status_severity ON analytics.hotspot (status, severity_band)",
    "CREATE INDEX ix_hotspot_detection_run ON analytics.hotspot (detection_run_id)",
    "CREATE INDEX ix_hotspot_detected_at ON analytics.hotspot (detected_at DESC)",
    # intervention
    "CREATE INDEX ix_intervention_location_completed ON intervention.intervention (location_id, completed_at)",
    "CREATE INDEX ix_intervention_status ON intervention.intervention (status)",
    "CREATE INDEX ix_intervention_cluster ON intervention.intervention (cluster_id)",
    "CREATE INDEX ix_intervention_hotspot ON intervention.intervention (hotspot_id)",
    "CREATE INDEX ix_intervention_completed ON intervention.intervention (completed_at DESC) WHERE status = 'completed'",
    "CREATE INDEX ix_intervention_report_link_report ON intervention.intervention_report_link (report_id)",
    "CREATE INDEX ix_impact_intervention ON intervention.impact_measurement (intervention_id, computed_at DESC)",
    "CREATE INDEX ix_impact_location ON intervention.impact_measurement (location_id)",
    "CREATE INDEX ix_impact_computed ON intervention.impact_measurement (computed_at DESC)",
    "CREATE INDEX ix_outcome_intervention ON intervention.intervention_outcome (intervention_id, assessed_at DESC)",
    "CREATE INDEX ix_outcome_status ON intervention.intervention_outcome (outcome_status)",
    # notify
    "CREATE INDEX ix_notification_unread ON notify.notification (recipient_user_id, created_at DESC) WHERE read_at IS NULL",
    "CREATE INDEX ix_notification_pending ON notify.notification (delivery_state) WHERE delivery_state = 'pending'",
    "CREATE INDEX ix_notification_role ON notify.notification (recipient_role, created_at DESC)",
    "CREATE INDEX ix_alert_active ON notify.broadcast_alert (issued_at DESC) WHERE is_active",
    "CREATE INDEX ix_alert_zone_active ON notify.broadcast_alert (zone_id) WHERE is_active",
    "CREATE UNIQUE INDEX uq_device_token_fcm ON notify.device_token (fcm_token)",
    "CREATE INDEX ix_device_token_user ON notify.device_token (user_id) WHERE is_active",
    # audit
    "CREATE INDEX ix_access_log_actor ON audit.access_log (actor_user_id, occurred_at DESC)",
    "CREATE INDEX ix_access_log_object ON audit.access_log (object_type, object_id, occurred_at DESC)",
    "CREATE INDEX ix_access_log_time ON audit.access_log (occurred_at DESC)",
    "CREATE INDEX ix_access_log_action ON audit.access_log (action)",
    "CREATE INDEX ix_access_log_denied ON audit.access_log (outcome) WHERE outcome = 'denied'",
    "CREATE INDEX ix_disclosure_report ON audit.identity_disclosure_log (report_id, disclosed_at DESC)",
    "CREATE INDEX ix_disclosure_actor ON audit.identity_disclosure_log (disclosed_to, disclosed_at DESC)",
]


# ---------------------------------------------------------------------------
# 5. Trigger functions and triggers
# ---------------------------------------------------------------------------

FUNCTIONS = [
    # 1. An anonymous report can never gain an attribution row.
    """
    CREATE FUNCTION identity.fn_attribution_requires_identified() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_mode public.submission_mode;
    BEGIN
        SELECT submission_mode INTO v_mode
          FROM core.report WHERE report_id = NEW.report_id;
        IF v_mode IS NULL THEN
            RAISE EXCEPTION 'report % does not exist', NEW.report_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF v_mode <> 'identified' THEN
            RAISE EXCEPTION
                'anonymity violation: report % is anonymous and cannot be attributed to a user',
                NEW.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 2. submission_mode and report_kind are immutable: no retroactive
    #    de-anonymisation, and no reclassifying an incident as a concern.
    """
    CREATE FUNCTION core.fn_report_mode_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.submission_mode IS DISTINCT FROM OLD.submission_mode THEN
            RAISE EXCEPTION
                'submission_mode is immutable (report %): a report cannot be re-anonymised or de-anonymised',
                OLD.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.report_kind IS DISTINCT FROM OLD.report_kind THEN
            RAISE EXCEPTION 'report_kind is immutable (report %)', OLD.report_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 3. reporter_contactable tracks contact_consent; forced FALSE with no
    #    attribution row.  Security learns whether a reporter can be reached
    #    without the identity schema entering the emergency path.
    """
    CREATE FUNCTION identity.fn_sync_reporter_contactable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            UPDATE core.report SET reporter_contactable = FALSE
             WHERE report_id = OLD.report_id;
            RETURN OLD;
        END IF;
        UPDATE core.report
           SET reporter_contactable = NEW.contact_consent
         WHERE report_id = NEW.report_id;
        RETURN NEW;
    END;
    $$
    """,
    # 4. emergency_dispatch rows exist only for emergency reports.
    """
    CREATE FUNCTION core.fn_dispatch_requires_emergency() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_is_emergency BOOLEAN;
    BEGIN
        SELECT is_emergency INTO v_is_emergency
          FROM core.report WHERE report_id = NEW.report_id;
        IF v_is_emergency IS NULL THEN
            RAISE EXCEPTION 'report % does not exist', NEW.report_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF NOT v_is_emergency THEN
            RAISE EXCEPTION 'report % is not an emergency; no dispatch record permitted',
                NEW.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 5. The database refuses to record a contact attempt against a reporter the
    #    system promised it could not contact.
    """
    CREATE FUNCTION core.fn_dispatch_contact_honours_flag() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_contactable BOOLEAN;
    BEGIN
        IF NOT NEW.contact_attempted THEN
            RETURN NEW;
        END IF;
        SELECT reporter_contactable INTO v_contactable
          FROM core.report WHERE report_id = NEW.report_id;
        IF NOT COALESCE(v_contactable, FALSE) THEN
            RAISE EXCEPTION
                'report % is marked not contactable; a contact attempt cannot be recorded',
                NEW.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 6. case_status_history is the source of truth; report.current_status is a cache.
    """
    CREATE FUNCTION core.fn_sync_report_status() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE core.report
           SET current_status = NEW.to_status,
               closed_at = CASE
                   WHEN NEW.to_status IN ('resolved', 'closed_no_action', 'withdrawn', 'duplicate')
                   THEN COALESCE(closed_at, NEW.changed_at)
                   ELSE NULL
               END
         WHERE report_id = NEW.report_id;
        RETURN NEW;
    END;
    $$
    """,
    # 7. current risk denormalised onto the report for the triage queue.
    """
    CREATE FUNCTION core.fn_sync_report_risk() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.is_current THEN
            UPDATE core.report
               SET current_risk_score = NEW.score,
                   current_risk_band  = NEW.band
             WHERE report_id = NEW.report_id;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 8. cluster counts and first/last seen.
    """
    CREATE FUNCTION core.fn_cluster_counts() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_cluster UUID := COALESCE(NEW.cluster_id, OLD.cluster_id);
    BEGIN
        UPDATE core.report_cluster c
           SET report_count = sub.n,
               first_report_at = sub.first_at,
               last_report_at  = sub.last_at
          FROM (
                SELECT count(*)          AS n,
                       min(r.occurred_at) AS first_at,
                       max(r.occurred_at) AS last_at
                  FROM core.cluster_member m
                  JOIN core.report r ON r.report_id = m.report_id
                 WHERE m.cluster_id = v_cluster
               ) AS sub
         WHERE c.cluster_id = v_cluster;
        RETURN COALESCE(NEW, OLD);
    END;
    $$
    """,
    # 9. Audit tables are append-only.  No FKs point at them and none point out,
    #    so nothing can cascade an UPDATE or DELETE past this.
    """
    CREATE FUNCTION audit.fn_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'audit table %.% is append-only; % is not permitted',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$
    """,
    # 10. case_status_history is append-only for direct writes, but must still be
    #     removable by the report cascade.  During a cascade the parent report row
    #     is already gone within the transaction, so its continued existence is a
    #     reliable signal that this is a direct tamper rather than a cascade.
    """
    CREATE FUNCTION core.fn_case_status_history_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'core.case_status_history is append-only; UPDATE is not permitted'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM core.report WHERE report_id = OLD.report_id) THEN
            RAISE EXCEPTION
                'core.case_status_history is append-only; rows are removable only by deleting the report'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END;
    $$
    """,
    # 11. authority_profile is for staff only.
    """
    CREATE FUNCTION identity.fn_authority_profile_role() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_role public.user_role;
    BEGIN
        SELECT role INTO v_role FROM identity.app_user WHERE user_id = NEW.user_id;
        IF v_role = 'student' THEN
            RAISE EXCEPTION 'user % is a student and cannot hold an authority profile',
                NEW.user_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 12. cases are never assigned to students.
    """
    CREATE FUNCTION core.fn_assignment_target_role() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_role public.user_role;
    BEGIN
        SELECT role INTO v_role FROM identity.app_user WHERE user_id = NEW.assigned_to;
        IF v_role IS NULL THEN
            RAISE EXCEPTION 'assignee % does not exist', NEW.assigned_to
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF v_role = 'student' THEN
            RAISE EXCEPTION 'cases cannot be assigned to a student (user %)', NEW.assigned_to
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 13. Evidence retention stamped from policy at insert, so amending the policy
    #     later governs new evidence without rewriting the terms applied to old.
    """
    CREATE FUNCTION evidence.fn_evidence_retention_stamp() RETURNS trigger
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
    """,
    # 14. The same for narratives, both the raw text and the redacted derivative.
    """
    CREATE FUNCTION core.fn_narrative_retention_stamp() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_narrative_days INTEGER;
        v_redacted_days  INTEGER;
    BEGIN
        IF NEW.retention_policy_key IS NULL THEN
            NEW.retention_policy_key := 'narrative_retention_days';
        END IF;
        SELECT policy_value::INTEGER INTO v_narrative_days
          FROM core.system_policy WHERE policy_key = NEW.retention_policy_key;
        SELECT policy_value::INTEGER INTO v_redacted_days
          FROM core.system_policy WHERE policy_key = 'narrative_redacted_retention_days';
        IF v_narrative_days IS NULL OR v_redacted_days IS NULL THEN
            RAISE EXCEPTION 'narrative retention policy is not defined in core.system_policy'
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        NEW.narrative_retention_days_applied :=
            COALESCE(NEW.narrative_retention_days_applied, v_narrative_days);
        NEW.redacted_retention_days_applied :=
            COALESCE(NEW.redacted_retention_days_applied, v_redacted_days);
        RETURN NEW;
    END;
    $$
    """,
    # 15. Once purged, a narrative cannot be restored, and the retention terms it
    #     was collected under cannot be edited after the fact.
    """
    CREATE FUNCTION core.fn_narrative_purge_integrity() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF OLD.narrative_purged_at IS NOT NULL
           AND (NEW.narrative IS NOT NULL OR NEW.narrative_purged_at IS NULL) THEN
            RAISE EXCEPTION 'narrative for report % was purged at % and cannot be restored',
                OLD.report_id, OLD.narrative_purged_at
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF OLD.redacted_purged_at IS NOT NULL
           AND (NEW.narrative_redacted IS NOT NULL OR NEW.redacted_purged_at IS NULL) THEN
            RAISE EXCEPTION 'redacted narrative for report % was purged and cannot be restored',
                OLD.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.narrative_retention_days_applied IS DISTINCT FROM OLD.narrative_retention_days_applied
           OR NEW.redacted_retention_days_applied IS DISTINCT FROM OLD.redacted_retention_days_applied THEN
            RAISE EXCEPTION
                'retention terms applied to report % are immutable; change core.system_policy for future rows instead',
                OLD.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 16. Phone filenames leak more than people expect.  Anonymous reports do not
    #     carry one.
    """
    CREATE FUNCTION evidence.fn_evidence_anonymous_no_filename() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_mode public.submission_mode;
    BEGIN
        IF NEW.original_filename IS NULL THEN
            RETURN NEW;
        END IF;
        SELECT submission_mode INTO v_mode
          FROM core.report WHERE report_id = NEW.report_id;
        IF v_mode = 'anonymous' THEN
            RAISE EXCEPTION
                'report % is anonymous; original_filename must not be stored (it can identify the reporter)',
                NEW.report_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 17. control_location_ids is an array and cannot carry a foreign key, so its
    #     members are validated here.  A control comparison against a location
    #     that does not exist is worse than no control comparison.
    """
    CREATE FUNCTION intervention.fn_impact_controls_exist() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_missing INTEGER;
    BEGIN
        IF NEW.control_location_ids IS NULL THEN
            RETURN NEW;
        END IF;
        SELECT c INTO v_missing
          FROM unnest(NEW.control_location_ids) AS c
         WHERE NOT EXISTS (SELECT 1 FROM core.campus_location l WHERE l.location_id = c)
         LIMIT 1;
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'control location % does not exist in core.campus_location', v_missing
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF NEW.location_id IS NOT NULL AND NEW.location_id = ANY (NEW.control_location_ids) THEN
            RAISE EXCEPTION
                'the treated location cannot also be one of its own controls (location %)',
                NEW.location_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # 18. A policy row must state its origin honestly: nothing may claim to be an
    #     institutional or regulatory requirement without a stated basis.
    """
    CREATE FUNCTION core.fn_policy_origin_evidence() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.origin <> 'prototype_default'
           AND (NEW.description IS NULL OR length(NEW.description) < 40) THEN
            RAISE EXCEPTION
                'policy % claims origin %; a description of at least 40 characters citing the source is required',
                NEW.policy_key, NEW.origin
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$
    """,
]

TRIGGERS = [
    """
    CREATE TRIGGER trg_attribution_requires_identified
        BEFORE INSERT OR UPDATE ON identity.report_attribution
        FOR EACH ROW EXECUTE FUNCTION identity.fn_attribution_requires_identified()
    """,
    """
    CREATE TRIGGER trg_report_mode_immutable
        BEFORE UPDATE ON core.report
        FOR EACH ROW EXECUTE FUNCTION core.fn_report_mode_immutable()
    """,
    """
    CREATE TRIGGER trg_sync_reporter_contactable
        AFTER INSERT OR UPDATE OR DELETE ON identity.report_attribution
        FOR EACH ROW EXECUTE FUNCTION identity.fn_sync_reporter_contactable()
    """,
    """
    CREATE TRIGGER trg_dispatch_requires_emergency
        BEFORE INSERT OR UPDATE ON core.emergency_dispatch
        FOR EACH ROW EXECUTE FUNCTION core.fn_dispatch_requires_emergency()
    """,
    """
    CREATE TRIGGER trg_dispatch_contact_honours_flag
        BEFORE INSERT OR UPDATE ON core.emergency_dispatch
        FOR EACH ROW EXECUTE FUNCTION core.fn_dispatch_contact_honours_flag()
    """,
    """
    CREATE TRIGGER trg_sync_report_status
        AFTER INSERT ON core.case_status_history
        FOR EACH ROW EXECUTE FUNCTION core.fn_sync_report_status()
    """,
    """
    CREATE TRIGGER trg_sync_report_risk
        AFTER INSERT OR UPDATE ON core.risk_assessment
        FOR EACH ROW EXECUTE FUNCTION core.fn_sync_report_risk()
    """,
    """
    CREATE TRIGGER trg_cluster_counts
        AFTER INSERT OR DELETE ON core.cluster_member
        FOR EACH ROW EXECUTE FUNCTION core.fn_cluster_counts()
    """,
    """
    CREATE TRIGGER trg_access_log_append_only
        BEFORE UPDATE OR DELETE ON audit.access_log
        FOR EACH ROW EXECUTE FUNCTION audit.fn_append_only()
    """,
    """
    CREATE TRIGGER trg_disclosure_log_append_only
        BEFORE UPDATE OR DELETE ON audit.identity_disclosure_log
        FOR EACH ROW EXECUTE FUNCTION audit.fn_append_only()
    """,
    """
    CREATE TRIGGER trg_case_status_history_append_only
        BEFORE UPDATE OR DELETE ON core.case_status_history
        FOR EACH ROW EXECUTE FUNCTION core.fn_case_status_history_append_only()
    """,
    """
    CREATE TRIGGER trg_authority_profile_role
        BEFORE INSERT OR UPDATE ON identity.authority_profile
        FOR EACH ROW EXECUTE FUNCTION identity.fn_authority_profile_role()
    """,
    """
    CREATE TRIGGER trg_assignment_target_role
        BEFORE INSERT OR UPDATE ON core.case_assignment
        FOR EACH ROW EXECUTE FUNCTION core.fn_assignment_target_role()
    """,
    """
    CREATE TRIGGER trg_evidence_retention_stamp
        BEFORE INSERT ON evidence.evidence_object
        FOR EACH ROW EXECUTE FUNCTION evidence.fn_evidence_retention_stamp()
    """,
    """
    CREATE TRIGGER trg_narrative_retention_stamp
        BEFORE INSERT ON core.report_narrative
        FOR EACH ROW EXECUTE FUNCTION core.fn_narrative_retention_stamp()
    """,
    """
    CREATE TRIGGER trg_narrative_purge_integrity
        BEFORE UPDATE ON core.report_narrative
        FOR EACH ROW EXECUTE FUNCTION core.fn_narrative_purge_integrity()
    """,
    """
    CREATE TRIGGER trg_evidence_anonymous_no_filename
        BEFORE INSERT OR UPDATE ON evidence.evidence_object
        FOR EACH ROW EXECUTE FUNCTION evidence.fn_evidence_anonymous_no_filename()
    """,
    """
    CREATE TRIGGER trg_impact_controls_exist
        BEFORE INSERT OR UPDATE ON intervention.impact_measurement
        FOR EACH ROW EXECUTE FUNCTION intervention.fn_impact_controls_exist()
    """,
    """
    CREATE TRIGGER trg_policy_origin_evidence
        BEFORE INSERT OR UPDATE ON core.system_policy
        FOR EACH ROW EXECUTE FUNCTION core.fn_policy_origin_evidence()
    """,
]


# ---------------------------------------------------------------------------
# 6. Public safety map view (k-anonymity suppression)
# ---------------------------------------------------------------------------

VIEWS = [
    # The only report-derived object the student community may query.
    #
    # k comes from core.system_policy ('map_min_aggregation_k', default 3) rather
    # than being a literal, so the threshold is configurable and lowering it is an
    # auditable act.  Locations below the threshold are OMITTED, not shown as zero
    # and not shown as "<3": a suppressed cell that announces its own suppression
    # still confirms that something happened there.
    #
    # Note what this view does not touch: core.report_narrative is not joined, so
    # the community layer cannot become an exfiltration channel for incident text
    # even if the view definition is later widened by accident.
    """
    CREATE VIEW analytics.v_public_safety_map AS
    SELECT
        l.location_id,
        l.name                                        AS location_name,
        l.latitude,
        l.longitude,
        date_trunc('week', r.occurred_at)             AS week_bucket,
        r.report_kind,
        count(*)::INTEGER                             AS report_count,
        (ARRAY['low', 'moderate', 'high', 'critical'])[
            max(CASE COALESCE(r.current_risk_band, 'low')
                    WHEN 'low' THEN 1 WHEN 'moderate' THEN 2
                    WHEN 'high' THEN 3 WHEN 'critical' THEN 4 END)
        ]::public.risk_band                           AS severity_band
    FROM core.report r
    JOIN core.campus_location l ON l.location_id = r.location_id
    WHERE r.current_status NOT IN ('duplicate', 'withdrawn')
      AND l.is_active
    GROUP BY l.location_id, l.name, l.latitude, l.longitude,
             date_trunc('week', r.occurred_at), r.report_kind
    HAVING count(*) >= (
        SELECT policy_value::INTEGER
          FROM core.system_policy
         WHERE policy_key = 'map_min_aggregation_k'
    )
    """,
]


# ---------------------------------------------------------------------------
# 7. Comments — the prohibitions live in the database, not only in a document
# ---------------------------------------------------------------------------

COMMENTS = [
    "COMMENT ON SCHEMA identity IS "
    "'Identity plane. Most restricted schema. Contains the only link between a report and a person.'",
    "COMMENT ON SCHEMA core IS "
    "'Incident plane. Report metadata and workflow. Contains no reporter identity.'",
    "COMMENT ON SCHEMA evidence IS "
    "'Evidence plane. Pointers to object storage. Contains no uploader identity.'",
    "COMMENT ON SCHEMA audit IS 'Append-only. No foreign keys in or out, by design.'",
    "COMMENT ON TABLE core.report IS "
    "'Central report table. Has NO user column, by design: anonymity is the absence of a row "
    "in identity.report_attribution, not a hidden or nullable column here.'",
    "COMMENT ON COLUMN core.report.reporter_contactable IS "
    "'Operational fact for responders: can this reporter be reached? FALSE is the default and "
    "the only legal value for an anonymous report (ck_report_anonymous_is_not_contactable), so "
    "security can answer the question without querying, or having access to, the identity schema.'",
    "COMMENT ON COLUMN core.report.reporter_relationship IS "
    "'CONTEXTUAL METADATA ONLY - vantage point, never credibility. PROHIBITED from: any "
    "credibility/trust/reliability score; any term or weight in core.risk_assessment.factors "
    "(enforced by ck_risk_factors_no_credibility_terms); any feature or filter in classification "
    "or similarity; ordering the triage queue; any UI treatment implying lesser seriousness. "
    "Deliberately unindexed so it does not become a ranking dimension.'",
    "COMMENT ON TABLE identity.report_attribution IS "
    "'The ONLY link between a report and a person. A row exists if and only if the reporter chose "
    "to be identified. Every read should write to audit.identity_disclosure_log.'",
    "COMMENT ON TABLE identity.submission_quota IS "
    "'Anti-spam counter, NOT a link. Records how many reports a user filed on a date, never which ones.'",
    "COMMENT ON TABLE core.report_narrative IS "
    "'Narrative firewall. Split from core.report so SELECT can be revoked independently: the "
    "analytics role computes every hotspot and impact measurement without read access here.'",
    "COMMENT ON TABLE evidence.evidence_object IS "
    "'Pointer to object storage. Has NO uploader column, by design: an uploader FK would "
    "de-anonymise every anonymous report that carried a photo.'",
    "COMMENT ON COLUMN evidence.evidence_object.original_filename IS "
    "'NULL for anonymous reports, enforced by trg_evidence_anonymous_no_filename. Phone filenames "
    "leak more than people expect.'",
    "COMMENT ON COLUMN core.risk_assessment.factors IS "
    "'Explanation of the score. Scores situations, not people. Rejects reporter_relationship and "
    "credibility-shaped keys at the top level and inside weights.'",
    "COMMENT ON TABLE notify.device_token IS "
    "'Bound to a user, never to a report. There is deliberately no report_id column: it would "
    "rebuild the identity link that anonymity exists to prevent.'",
    "COMMENT ON TABLE audit.access_log IS "
    "'Append-only. Carries no foreign keys because every ON DELETE action conflicts with "
    "append-only enforcement. ip_hash is an HMAC, never a raw address. detail must never carry "
    "narrative text.'",
    "COMMENT ON TABLE audit.identity_disclosure_log IS "
    "'Every occasion on which a reporter identity was resolved. A written purpose is mandatory. "
    "Applies only to identified reports: anonymous reports have no identity to disclose.'",
    "COMMENT ON COLUMN core.campus_location.coordinate_status IS "
    "'A location cannot become is_active without verified coordinates and a recorded source. "
    "See CAMPUS_LOCATIONS.md: as of the initial migration, zero coordinates are verified.'",
    "COMMENT ON VIEW analytics.v_public_safety_map IS "
    "'The only report-derived object the student community may query. Locations below "
    "system_policy.map_min_aggregation_k (default 3) are omitted entirely, not shown as zero. "
    "Never joins core.report_narrative.'",
    "COMMENT ON TABLE intervention.impact_measurement IS "
    "'Before/after comparison WITH controls. A drop in reports at a treated location is ambiguous "
    "on its own - the area may be safer, or students may have stopped trusting the system. A "
    "difference-in-differences row cannot be stored without the control figures it claims.'",
]


# ---------------------------------------------------------------------------
# 8. Seed: core.system_policy
#
# Policy rows only.  They are seeded here because the retention triggers read
# from this table and would otherwise fail on the first narrative or evidence
# insert -- a functional dependency, not editorial content.
#
# NO campus locations, zones, or coordinates are seeded.  CAMPUS_LOCATIONS.md
# contains zero verified coordinates and none will be invented.
# ---------------------------------------------------------------------------

POLICY_SEED = [
    (
        "evidence_retention_days", "365", "integer", "prototype_default",
        "Days after case closure before evidence is purged. PROTOTYPE POLICY, not a "
        "university-mandated requirement: the project specification defines none.",
        "1", "3650",
    ),
    (
        "evidence_retention_open_case_days", "1095", "integer", "prototype_default",
        "Ceiling for evidence on a case that never closes. Prototype policy.",
        "1", "3650",
    ),
    (
        "narrative_retention_days", "365", "integer", "prototype_default",
        "Days after case closure before the raw narrative is purged. PROTOTYPE POLICY, "
        "not a university-mandated requirement.",
        "1", "3650",
    ),
    (
        "narrative_redacted_retention_days", "730", "integer", "prototype_default",
        "Days after case closure before the redacted narrative is purged. Set equal to "
        "narrative_retention_days to purge both together. Prototype policy.",
        "1", "3650",
    ),
    (
        "narrative_retention_open_case_days", "1095", "integer", "prototype_default",
        "Ceiling for narratives on a case that never closes. Prototype policy.",
        "1", "3650",
    ),
    (
        "map_min_aggregation_k", "3", "integer", "prototype_default",
        "Minimum reports before a location appears on the public safety map. PRIVACY-PRESERVING "
        "PROTOTYPE POLICY. Below this, an aggregate stops being an aggregate and can identify "
        "both the incident and the reporter. Locations below k are omitted entirely.",
        "3", "50",
    ),
    (
        "map_time_bucket", "week", "text", "prototype_default",
        "Coarsest-safe time granularity for public display. Exact timestamps never reach the "
        "public layer. Prototype policy.",
        None, None,
    ),
    (
        "anonymous_token_ttl_days", "180", "integer", "prototype_default",
        "Lifetime of an anonymous status-lookup token. Prototype policy.",
        "1", "3650",
    ),
    (
        "emergency_callback_ttl_hours", "24", "integer", "prototype_default",
        "Lifetime of a voluntarily supplied emergency callback number before auto-purge. "
        "Prototype policy.",
        "1", "720",
    ),
    (
        "related_similarity_threshold", "0.72", "numeric", "prototype_default",
        "Cosine similarity at or above which two reports are linked as related. Prototype policy.",
        "0", "1",
    ),
    (
        "duplicate_similarity_threshold", "0.88", "numeric", "prototype_default",
        "Cosine similarity at or above which two reports are linked as duplicates. Prototype policy.",
        "0", "1",
    ),
    (
        "impact_window_days", "30", "integer", "prototype_default",
        "Default equal-length window either side of an intervention completion date. Prototype policy.",
        "7", "365",
    ),
    (
        "daily_report_quota", "5", "integer", "prototype_default",
        "Maximum submissions per user per day. Enforced via identity.submission_quota, which "
        "counts without linking. Prototype policy.",
        "1", "100",
    ),
]


def _policy_seed_ddl() -> list[str]:
    rows = []
    for key, value, vtype, origin, desc, lo, hi in POLICY_SEED:
        def q(v: str | None) -> str:
            return "NULL" if v is None else "'{}'".format(v.replace("'", "''"))

        rows.append(
            "({}, {}, {}, '{}', {}, {}, {})".format(
                q(key), q(value), q(vtype), origin, q(desc), q(lo), q(hi)
            )
        )
    return [
        "INSERT INTO core.system_policy "
        "(policy_key, policy_value, value_type, origin, description, min_value, max_value) "
        "VALUES\n  " + ",\n  ".join(rows)
    ]


# ---------------------------------------------------------------------------

def upgrade() -> None:
    statements: list[str] = (
        SCHEMAS
        + _enum_ddl()
        + TABLES
        + INDEXES
        + FUNCTIONS
        + TRIGGERS
        + VIEWS
        + COMMENTS
        + _policy_seed_ddl()
    )
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    # Dropping the schemas with CASCADE removes every table, index, view, trigger
    # and trigger function inside them in one step.  The enum types live in
    # public and are dropped explicitly afterwards.
    for schema in reversed(SCHEMA_NAMES):
        op.execute("DROP SCHEMA IF EXISTS {} CASCADE".format(schema))
    for name, _ in reversed(ENUMS):
        op.execute("DROP TYPE IF EXISTS public.{}".format(name))
