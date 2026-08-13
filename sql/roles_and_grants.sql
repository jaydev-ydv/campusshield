-- CampusShield — database roles and privilege separation
--
-- NOT part of the Alembic migration, deliberately.  Roles are cluster-level
-- objects, not database objects: putting CREATE ROLE in a migration makes it
-- fail on the second environment that already has the role, and makes rollback
-- ambiguous (does downgrading a schema drop a role another database uses?).
-- Run this once per cluster, as a superuser, after the first migration.
--
--   psql -d campusshield -f sql/roles_and_grants.sql
--
-- CHANGE THE PASSWORDS BEFORE RUNNING.  They are placeholders.
--
-- ---------------------------------------------------------------------------
-- What this file buys you
-- ---------------------------------------------------------------------------
-- The privacy separation in DATABASE.md is only real if the database enforces
-- it.  Schemas alone do not: a single superuser connection can read everything.
-- These grants make the separation concrete, and the single most demonstrable
-- property of the whole system is the cs_analytics role below —
--
--   it can compute every hotspot and every impact measurement in the system
--   without the ability to read one student's account of what happened to them.
--
-- That is a GRANT table, not a promise. It is worth showing to an examiner.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 1. Roles
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cs_app') THEN
        CREATE ROLE cs_app LOGIN PASSWORD 'CHANGE_ME_app';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cs_ml_worker') THEN
        CREATE ROLE cs_ml_worker LOGIN PASSWORD 'CHANGE_ME_ml';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cs_analytics') THEN
        CREATE ROLE cs_analytics LOGIN PASSWORD 'CHANGE_ME_analytics';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cs_readonly_demo') THEN
        CREATE ROLE cs_readonly_demo LOGIN PASSWORD 'CHANGE_ME_demo';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Baseline: nobody reaches anything by default
-- ---------------------------------------------------------------------------
REVOKE ALL ON SCHEMA identity, core, evidence, ml, analytics, intervention, notify, audit
    FROM PUBLIC;

GRANT USAGE ON SCHEMA core, analytics, intervention, ml TO
    cs_app, cs_ml_worker, cs_analytics, cs_readonly_demo;
GRANT USAGE ON SCHEMA identity, evidence, notify TO cs_app;
GRANT USAGE ON SCHEMA evidence TO cs_app;
GRANT USAGE ON SCHEMA notify  TO cs_app;
GRANT USAGE ON SCHEMA audit   TO cs_app, cs_ml_worker, cs_analytics;

-- ---------------------------------------------------------------------------
-- 3. cs_app — the Flask application.  Full read/write except audit.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA
    identity, core, evidence, ml, analytics, intervention, notify TO cs_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA
    identity, core, evidence, ml, analytics, intervention, notify TO cs_app;

-- Audit is INSERT-only for everyone.  The append-only triggers block UPDATE and
-- DELETE at the row level; revoking the privileges as well means an attempt
-- fails before a trigger ever has to fire.
GRANT INSERT, SELECT ON ALL TABLES IN SCHEMA audit TO cs_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA audit TO cs_app;

-- ---------------------------------------------------------------------------
-- 4. cs_ml_worker — classification, embeddings, clustering.
--    Reads narratives (it must, to classify them).  Never touches identity.
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA core TO cs_ml_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ml TO cs_ml_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics TO cs_ml_worker;
GRANT UPDATE ON core.report TO cs_ml_worker;            -- denormalised risk/cluster fields
GRANT INSERT ON core.report_link, core.report_cluster,
                core.cluster_member, core.risk_assessment TO cs_ml_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ml, analytics, core TO cs_ml_worker;
GRANT INSERT ON ALL TABLES IN SCHEMA audit TO cs_ml_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA audit TO cs_ml_worker;

-- ---------------------------------------------------------------------------
-- 5. cs_analytics — the Administration dashboard.
--    THE POINT OF THIS FILE.  Full analytical reach, zero narrative access.
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA core, ml, intervention TO cs_analytics;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA analytics TO cs_analytics;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics TO cs_analytics;
GRANT INSERT ON ALL TABLES IN SCHEMA audit TO cs_analytics;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA audit TO cs_analytics;

-- The narrative firewall.
REVOKE ALL ON core.report_narrative FROM cs_analytics;

-- Identity is not merely un-granted, it is unreachable: without USAGE on the
-- schema, even a correctly-guessed table name resolves to nothing.
REVOKE ALL ON SCHEMA identity FROM cs_analytics;
REVOKE ALL ON SCHEMA evidence FROM cs_analytics;

-- ---------------------------------------------------------------------------
-- 6. cs_readonly_demo — for a viva or a walkthrough.  Sees structure and
--    aggregates, never an account of an incident.
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA core, ml, analytics, intervention TO cs_readonly_demo;
REVOKE ALL ON core.report_narrative FROM cs_readonly_demo;
REVOKE ALL ON SCHEMA identity, evidence, audit, notify FROM cs_readonly_demo;

-- ---------------------------------------------------------------------------
-- 7. Future tables inherit the same rules
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA core, ml, analytics, intervention
    GRANT SELECT ON TABLES TO cs_analytics, cs_readonly_demo;
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT ON TABLES TO cs_ml_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit
    GRANT INSERT ON TABLES TO cs_app, cs_ml_worker, cs_analytics;

-- ---------------------------------------------------------------------------
-- 8. Verify — run this and read the output aloud
-- ---------------------------------------------------------------------------
-- Expect: f  (cs_analytics cannot read narratives)
SELECT has_table_privilege('cs_analytics', 'core.report_narrative', 'SELECT')
       AS analytics_can_read_narratives;

-- Expect: t  (but it can read everything it needs for hotspots and impact)
SELECT has_table_privilege('cs_analytics', 'core.report', 'SELECT')
       AS analytics_can_read_report_metadata,
       has_table_privilege('cs_analytics', 'analytics.hotspot', 'SELECT')
       AS analytics_can_read_hotspots,
       has_table_privilege('cs_analytics', 'intervention.impact_measurement', 'SELECT')
       AS analytics_can_read_impact;

-- Expect: f  (identity is out of reach entirely)
SELECT has_schema_privilege('cs_analytics', 'identity', 'USAGE')
       AS analytics_can_reach_identity_schema;

-- Expect: f, f  (audit is insert-only even for the application)
SELECT has_table_privilege('cs_app', 'audit.access_log', 'UPDATE') AS app_can_update_audit,
       has_table_privilege('cs_app', 'audit.access_log', 'DELETE') AS app_can_delete_audit;
