-- CampusShield — proposed report taxonomy
--
-- OPTIONAL and NOT applied by the migration.  This is a proposal for review,
-- not approved content: the categories, their severities, and above all their
-- routing decide which authority sees which report, and that is an
-- institutional judgement rather than a technical one.  Read it, change it,
-- then apply:
--
--   psql -d campusshield -f sql/seed_report_categories.sql
--
-- Only core.system_policy is seeded by the migration itself, because the
-- retention triggers read from it and would otherwise fail on the first insert.
--
-- NO campus locations or coordinates are seeded anywhere.  See CAMPUS_LOCATIONS.md:
-- zero coordinates are verified, and none will be invented.
--
-- ---------------------------------------------------------------------------
-- Points to settle before applying
-- ---------------------------------------------------------------------------
-- 1. routes_to_role — harassment categories route to 'icc', environmental ones
--    to 'security'.  Confirm this matches how Presidency University actually
--    divides responsibility between the ICC and campus security.
-- 2. requires_confidentiality — restricts the narrative to ICC via RLS.  Set on
--    every harassment category here.
-- 3. emergency_eligible — which categories may be raised in emergency mode.
--    A broken streetlight is not an emergency; the schema already enforces that
--    only 'incident' categories can carry this flag.
-- 4. base_severity (1-5) feeds risk scoring.  These are starting values.

\set ON_ERROR_STOP on

BEGIN;

-- Incident categories -------------------------------------------------------
INSERT INTO core.report_category
    (code, label, kind, routes_to_role, base_severity, requires_confidentiality, emergency_eligible)
VALUES
    ('HARASS_VERBAL',   'Verbal harassment or catcalling',        'incident', 'icc',      3, TRUE,  TRUE),
    ('HARASS_PHYSICAL', 'Unwanted physical contact',              'incident', 'icc',      5, TRUE,  TRUE),
    ('STALKING',        'Following or stalking',                  'incident', 'icc',      4, TRUE,  TRUE),
    ('HARASS_DIGITAL',  'Online or phone harassment',             'incident', 'icc',      3, TRUE,  FALSE),
    ('INTIMIDATION',    'Threats or intimidation',                'incident', 'icc',      4, TRUE,  TRUE),
    ('RAGGING',         'Ragging or hazing',                      'incident', 'icc',      4, TRUE,  TRUE),
    ('VOYEURISM',       'Filming or photography without consent', 'incident', 'icc',      5, TRUE,  TRUE),
    ('TRESPASS',        'Unauthorised person on campus',          'incident', 'security', 3, FALSE, TRUE)
ON CONFLICT (code) DO NOTHING;

-- Environmental safety concerns ---------------------------------------------
-- The deck's motivation section singles these out: surfacing hazards before
-- they lead to incidents is what separates this from a complaint box.
INSERT INTO core.report_category
    (code, label, kind, routes_to_role, base_severity, requires_confidentiality, emergency_eligible)
VALUES
    ('LIGHTING_POOR',   'Poor or broken lighting',                'concern', 'security', 3, FALSE, FALSE),
    ('ISOLATED_AREA',   'Isolated or unsafe area',                'concern', 'security', 3, FALSE, FALSE),
    ('CCTV_GAP',        'No camera coverage where expected',      'concern', 'security', 2, FALSE, FALSE),
    ('BLOCKED_ROUTE',   'Blocked or unsafe walkway',              'concern', 'security', 2, FALSE, FALSE),
    ('ACCESS_CONTROL',  'Gate or door left unsecured',            'concern', 'security', 3, FALSE, FALSE),
    ('TRANSPORT_SAFETY','Bus stop or transport safety concern',   'concern', 'security', 3, FALSE, FALSE)
ON CONFLICT (code) DO NOTHING;

-- Fallback ------------------------------------------------------------------
-- A student who cannot find their situation in a list will either pick the
-- nearest wrong option or abandon the form.  Both are worse than an 'other'
-- bucket that an authority triages by hand, and repeated 'other' reports are
-- themselves the signal that the taxonomy needs a new row.
INSERT INTO core.report_category
    (code, label, kind, routes_to_role, base_severity, requires_confidentiality, emergency_eligible)
VALUES
    ('OTHER_INCIDENT', 'Other safety incident', 'incident', 'icc',      3, TRUE,  TRUE),
    ('OTHER_CONCERN',  'Other safety concern',  'concern',  'security', 2, FALSE, FALSE)
ON CONFLICT (code) DO NOTHING;

COMMIT;

SELECT kind, routes_to_role, count(*) AS categories
  FROM core.report_category GROUP BY kind, routes_to_role ORDER BY kind, routes_to_role;
