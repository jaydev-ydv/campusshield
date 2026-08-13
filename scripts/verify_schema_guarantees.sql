-- CampusShield — schema guarantee verification
--
-- Exercises the structural privacy and integrity guarantees the schema is
-- supposed to enforce.  Every test runs inside one transaction that is rolled
-- back at the end, so this leaves no data behind and is safe to run against any
-- freshly migrated database.
--
--   psql -d campusshield -f scripts/verify_schema_guarantees.sql
--
-- Any test that prints FAIL, or any unexpected error, means a guarantee that
-- DATABASE.md claims is structural has stopped being structural.
--
-- The coordinates used below are 0.0 / 0.0 — deliberately synthetic, never
-- written outside this rolled-back transaction, and not campus data.

\set ON_ERROR_STOP on
\timing off
SET client_min_messages = NOTICE;

BEGIN;

-- ---------------------------------------------------------------------------
-- Fixtures
-- ---------------------------------------------------------------------------
INSERT INTO identity.app_user (user_id, firebase_uid, role, institutional_email, display_name)
VALUES ('11111111-1111-1111-1111-111111111111', 'fb-student', 'student', 'student@test.local', NULL),
       ('22222222-2222-2222-2222-222222222222', 'fb-security', 'security', 'security@test.local', 'Security Desk'),
       ('33333333-3333-3333-3333-333333333333', 'fb-icc', 'icc', 'icc@test.local', 'ICC Member');

INSERT INTO core.campus_location (code, name, location_type, latitude, longitude,
                                  coordinate_status, coordinate_source, coordinate_captured_at, is_active)
VALUES ('TEST-A', 'Test Location A', 'academic', 0.0, 0.0, 'verified', 'synthetic-test', now(), TRUE),
       ('TEST-B', 'Test Location B', 'parking',  0.0, 0.0, 'verified', 'synthetic-test', now(), TRUE);

INSERT INTO core.campus_location (code, name, location_type)
VALUES ('TEST-UNSURVEYED', 'Unsurveyed Location', 'gate');

INSERT INTO core.report_category (code, label, kind, routes_to_role, base_severity,
                                  requires_confidentiality, emergency_eligible)
VALUES ('TEST_HARASS', 'Test harassment', 'incident', 'icc', 4, TRUE, TRUE),
       ('TEST_LIGHT', 'Test poor lighting', 'concern', 'security', 2, FALSE, FALSE);

-- An anonymous report and an identified report at the same location.
INSERT INTO core.report (report_id, public_ref, report_kind, submission_mode,
                         declared_category_id, location_id, occurred_at,
                         occurred_hour, occurred_dow)
SELECT 'aaaaaaaa-0000-0000-0000-000000000001', 'CS-2026-AAAAAA', 'incident', 'anonymous',
       c.category_id, l.location_id, now() - interval '2 hours', 20, 3
  FROM core.report_category c, core.campus_location l
 WHERE c.code = 'TEST_HARASS' AND l.code = 'TEST-A';

INSERT INTO core.report (report_id, public_ref, report_kind, submission_mode,
                         declared_category_id, location_id, occurred_at,
                         occurred_hour, occurred_dow)
SELECT 'bbbbbbbb-0000-0000-0000-000000000002', 'CS-2026-BBBBBB', 'incident', 'identified',
       c.category_id, l.location_id, now() - interval '3 hours', 19, 3
  FROM core.report_category c, core.campus_location l
 WHERE c.code = 'TEST_HARASS' AND l.code = 'TEST-A';

INSERT INTO core.report_narrative (report_id, narrative)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'A test narrative long enough to pass the length check.'),
       ('bbbbbbbb-0000-0000-0000-000000000002', 'Another test narrative of sufficient length.');


-- ---------------------------------------------------------------------------
-- 1. ANONYMITY — an anonymous report cannot be attributed to a person
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        INSERT INTO identity.report_attribution (report_id, user_id)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001',
                '11111111-1111-1111-1111-111111111111');
        RAISE EXCEPTION 'FAIL 1: an anonymous report accepted an attribution row';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 1: anonymous report rejected attribution';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 2. ANONYMITY — submission_mode is immutable (no retroactive de-anonymisation)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        UPDATE core.report SET submission_mode = 'identified'
         WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
        RAISE EXCEPTION 'FAIL 2: submission_mode was changed after insert';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 2: submission_mode is immutable';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 3. ANONYMITY — reporter_contactable cannot be true for an anonymous report
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        UPDATE core.report SET reporter_contactable = TRUE
         WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
        RAISE EXCEPTION 'FAIL 3: anonymous report was marked contactable';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 3: anonymous report cannot be marked contactable';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 4. IDENTIFIED — attribution syncs contactability from consent
-- ---------------------------------------------------------------------------
INSERT INTO identity.report_attribution (report_id, user_id, contact_consent)
VALUES ('bbbbbbbb-0000-0000-0000-000000000002',
        '11111111-1111-1111-1111-111111111111', TRUE);

DO $$
DECLARE v BOOLEAN;
BEGIN
    SELECT reporter_contactable INTO v FROM core.report
     WHERE report_id = 'bbbbbbbb-0000-0000-0000-000000000002';
    IF v THEN RAISE NOTICE 'PASS 4a: identified + consent => contactable';
    ELSE RAISE EXCEPTION 'FAIL 4a: consent did not propagate to reporter_contactable';
    END IF;

    UPDATE identity.report_attribution SET contact_consent = FALSE
     WHERE report_id = 'bbbbbbbb-0000-0000-0000-000000000002';
    SELECT reporter_contactable INTO v FROM core.report
     WHERE report_id = 'bbbbbbbb-0000-0000-0000-000000000002';
    IF NOT v THEN RAISE NOTICE 'PASS 4b: withdrawing consent clears contactability';
    ELSE RAISE EXCEPTION 'FAIL 4b: withdrawn consent left reporter contactable';
    END IF;

    UPDATE identity.report_attribution SET contact_consent = TRUE
     WHERE report_id = 'bbbbbbbb-0000-0000-0000-000000000002';
END $$;

-- ---------------------------------------------------------------------------
-- 5. EMERGENCY + ANONYMOUS — permitted, and dispatch gets what it needs
-- ---------------------------------------------------------------------------
INSERT INTO core.report (report_id, public_ref, report_kind, submission_mode,
                         declared_category_id, location_id, occurred_at,
                         occurred_hour, occurred_dow, is_emergency, is_ongoing)
SELECT 'cccccccc-0000-0000-0000-000000000003', 'CS-2026-CCCCCC', 'incident', 'anonymous',
       c.category_id, l.location_id, now(), 21, 3, TRUE, TRUE
  FROM core.report_category c, core.campus_location l
 WHERE c.code = 'TEST_HARASS' AND l.code = 'TEST-B';

DO $$
DECLARE v RECORD;
BEGIN
    SELECT r.is_emergency, r.is_ongoing, r.reporter_contactable, l.name, l.dispatch_note
      INTO v
      FROM core.report r JOIN core.campus_location l ON l.location_id = r.location_id
     WHERE r.report_id = 'cccccccc-0000-0000-0000-000000000003';
    IF v.is_emergency AND v.is_ongoing AND NOT v.reporter_contactable THEN
        RAISE NOTICE 'PASS 5: anonymous emergency accepted; location known, reporter not contactable';
    ELSE
        RAISE EXCEPTION 'FAIL 5: anonymous emergency state incorrect';
    END IF;
END $$;

INSERT INTO core.emergency_dispatch (report_id, raised_at)
VALUES ('cccccccc-0000-0000-0000-000000000003', now());

-- 5b. The database refuses to record a contact attempt against a reporter it
--     promised could not be contacted.
DO $$
BEGIN
    BEGIN
        UPDATE core.emergency_dispatch SET contact_attempted = TRUE
         WHERE report_id = 'cccccccc-0000-0000-0000-000000000003';
        RAISE EXCEPTION 'FAIL 5b: contact attempt recorded against a non-contactable reporter';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 5b: contact attempt refused for non-contactable reporter';
    END;
END $$;

-- 5c. Dispatch records only exist for emergencies.
DO $$
BEGIN
    BEGIN
        INSERT INTO core.emergency_dispatch (report_id, raised_at)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001', now());
        RAISE EXCEPTION 'FAIL 5c: dispatch created for a non-emergency report';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 5c: dispatch refused for non-emergency report';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 6. E1 — reporter_relationship is stored, and barred from risk factors
-- ---------------------------------------------------------------------------
UPDATE core.report SET reporter_relationship = 'witness'
 WHERE report_id = 'cccccccc-0000-0000-0000-000000000003';

DO $$
BEGIN
    BEGIN
        INSERT INTO core.risk_assessment (report_id, score, band, scorer_version, factors)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 50, 'moderate', 'test-v1',
                '{"category_severity": 4, "reporter_relationship": "witness"}'::jsonb);
        RAISE EXCEPTION 'FAIL 6a: reporter_relationship accepted as a risk factor';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 6a: reporter_relationship rejected from risk factors';
    END;

    BEGIN
        INSERT INTO core.risk_assessment (report_id, score, band, scorer_version, factors)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 50, 'moderate', 'test-v1',
                '{"category_severity": 4, "weights": {"credibility": 0.3}}'::jsonb);
        RAISE EXCEPTION 'FAIL 6b: a credibility weight was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 6b: credibility weight rejected from risk factors';
    END;
END $$;

-- 6c. A legitimate risk assessment is accepted and denormalises onto the report.
INSERT INTO core.risk_assessment (report_id, score, band, scorer_version, factors, trigger_reason)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 72.5, 'high', 'rules-v1',
        '{"category_severity": 4, "recurrence_at_location_90d": 6, "night_hours": true,
          "weights": {"severity": 0.3, "recurrence": 0.35}}'::jsonb, 'initial');

DO $$
DECLARE v NUMERIC; b public.risk_band;
BEGIN
    SELECT current_risk_score, current_risk_band INTO v, b FROM core.report
     WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
    IF v = 72.5 AND b = 'high' THEN
        RAISE NOTICE 'PASS 6c: risk assessment denormalised onto the report';
    ELSE
        RAISE EXCEPTION 'FAIL 6c: risk sync did not fire (score=%, band=%)', v, b;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 7. RELATED REPORTS — canonical ordering makes deduplication actually work
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        INSERT INTO core.report_link (report_id_a, report_id_b, link_type, similarity, method)
        VALUES ('cccccccc-0000-0000-0000-000000000003',
                'aaaaaaaa-0000-0000-0000-000000000001', 'related', 0.8, 'embedding_cosine');
        RAISE EXCEPTION 'FAIL 7a: out-of-order report link accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 7a: non-canonical link order rejected';
    END;
END $$;

INSERT INTO core.report_link (report_id_a, report_id_b, link_type, similarity, method)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001',
        'cccccccc-0000-0000-0000-000000000003', 'related', 0.8, 'embedding_cosine');

DO $$
BEGIN
    BEGIN
        INSERT INTO core.report_link (report_id_a, report_id_b, link_type, similarity, method)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001',
                'cccccccc-0000-0000-0000-000000000003', 'related', 0.9, 'manual');
        RAISE EXCEPTION 'FAIL 7b: duplicate link pair accepted';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'PASS 7b: duplicate link pair rejected';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 8. CASE MANAGEMENT — status history drives report status; students are not
--    assignable; history is append-only
-- ---------------------------------------------------------------------------
INSERT INTO core.case_status_history (report_id, from_status, to_status, changed_by, remark)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'submitted', 'under_review',
        '33333333-3333-3333-3333-333333333333', 'Reviewing');

DO $$
DECLARE v public.report_status;
BEGIN
    SELECT current_status INTO v FROM core.report
     WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
    IF v = 'under_review' THEN RAISE NOTICE 'PASS 8a: status history drives report status';
    ELSE RAISE EXCEPTION 'FAIL 8a: status not synced (got %)', v;
    END IF;

    BEGIN
        UPDATE core.case_status_history SET remark = 'tampered'
         WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
        RAISE EXCEPTION 'FAIL 8b: case status history was updated';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 8b: case status history is append-only';
    END;

    BEGIN
        INSERT INTO core.case_assignment (report_id, assigned_to, assigned_role)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001',
                '11111111-1111-1111-1111-111111111111', 'icc');
        RAISE EXCEPTION 'FAIL 8c: a case was assigned to a student';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 8c: cases cannot be assigned to students';
    END;
END $$;

INSERT INTO core.case_assignment (report_id, assigned_to, assigned_role)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001',
        '33333333-3333-3333-3333-333333333333', 'icc');

DO $$
BEGIN
    BEGIN
        INSERT INTO core.case_assignment (report_id, assigned_to, assigned_role)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001',
                '22222222-2222-2222-2222-222222222222', 'security');
        RAISE EXCEPTION 'FAIL 8d: two active assignments on one report';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'PASS 8d: only one active assignment per report';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 9. EVIDENCE — retention stamped from policy; no filename on anonymous reports
-- ---------------------------------------------------------------------------
INSERT INTO evidence.evidence_object (report_id, storage_backend, storage_path,
                                      content_type, byte_size)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'local', 'test/evidence-1.jpg',
        'image/jpeg', 12345);

DO $$
DECLARE v INTEGER; k TEXT;
BEGIN
    SELECT retention_days_applied, retention_policy_key INTO v, k
      FROM evidence.evidence_object WHERE storage_path = 'test/evidence-1.jpg';
    IF v = 365 AND k = 'evidence_retention_days' THEN
        RAISE NOTICE 'PASS 9a: evidence retention stamped from system_policy (% days)', v;
    ELSE
        RAISE EXCEPTION 'FAIL 9a: retention not stamped (days=%, key=%)', v, k;
    END IF;

    BEGIN
        INSERT INTO evidence.evidence_object (report_id, storage_backend, storage_path,
                                              content_type, byte_size, original_filename)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'local', 'test/evidence-2.jpg',
                'image/jpeg', 999, 'IMG_20260810_hostel.jpg');
        RAISE EXCEPTION 'FAIL 9b: original filename stored against an anonymous report';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 9b: filename refused for anonymous report';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 10. NARRATIVE RETENTION — stamped from policy, terms immutable, purge one-way
-- ---------------------------------------------------------------------------
DO $$
DECLARE nd INTEGER; rd INTEGER;
BEGIN
    SELECT narrative_retention_days_applied, redacted_retention_days_applied INTO nd, rd
      FROM core.report_narrative WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
    IF nd = 365 AND rd = 730 THEN
        RAISE NOTICE 'PASS 10a: narrative retention stamped (raw % / redacted % days)', nd, rd;
    ELSE
        RAISE EXCEPTION 'FAIL 10a: narrative retention not stamped (% / %)', nd, rd;
    END IF;

    BEGIN
        UPDATE core.report_narrative SET narrative_retention_days_applied = 30
         WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
        RAISE EXCEPTION 'FAIL 10b: retention terms were rewritten after the fact';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 10b: applied retention terms are immutable';
    END;

    BEGIN
        UPDATE core.report_narrative SET narrative = NULL
         WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
        RAISE EXCEPTION 'FAIL 10c: narrative nulled without a purge timestamp';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 10c: a NULL narrative must be an explained NULL';
    END;
END $$;

-- A legitimate purge, then an attempt to undo it.
UPDATE core.report_narrative
   SET narrative = NULL, narrative_purged_at = now(), purge_reason = 'retention_expiry'
 WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';

DO $$
DECLARE wc INTEGER;
BEGIN
    BEGIN
        UPDATE core.report_narrative
           SET narrative = 'restored text that should not be possible',
               narrative_purged_at = NULL
         WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
        RAISE EXCEPTION 'FAIL 10d: a purged narrative was restored';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 10d: a purged narrative cannot be restored';
    END;

    SELECT count(*) INTO wc FROM core.report_narrative
     WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
    IF wc = 1 THEN
        RAISE NOTICE 'PASS 10e: purge nulls the text and keeps the row';
    ELSE
        RAISE EXCEPTION 'FAIL 10e: purge removed the row';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 11. AUDIT — append-only, both tables
-- ---------------------------------------------------------------------------
INSERT INTO audit.access_log (actor_user_id, actor_role, action, object_type, object_id, outcome)
VALUES ('33333333-3333-3333-3333-333333333333', 'icc', 'narrative.read', 'report',
        'aaaaaaaa-0000-0000-0000-000000000001', 'success');

INSERT INTO audit.identity_disclosure_log (report_id, disclosed_to, purpose)
VALUES ('bbbbbbbb-0000-0000-0000-000000000002',
        '33333333-3333-3333-3333-333333333333', 'ICC proceeding 2026/TEST');

DO $$
BEGIN
    BEGIN
        UPDATE audit.access_log SET outcome = 'denied' WHERE action = 'narrative.read';
        RAISE EXCEPTION 'FAIL 11a: audit.access_log was updated';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 11a: audit.access_log is append-only (UPDATE blocked)';
    END;
    BEGIN
        DELETE FROM audit.access_log WHERE action = 'narrative.read';
        RAISE EXCEPTION 'FAIL 11b: audit.access_log was deleted from';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 11b: audit.access_log is append-only (DELETE blocked)';
    END;
    BEGIN
        DELETE FROM audit.identity_disclosure_log;
        RAISE EXCEPTION 'FAIL 11c: identity disclosure log was deleted from';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 11c: identity_disclosure_log is append-only';
    END;
    BEGIN
        INSERT INTO audit.identity_disclosure_log (report_id, disclosed_to, purpose)
        VALUES ('bbbbbbbb-0000-0000-0000-000000000002',
                '33333333-3333-3333-3333-333333333333', 'short');
        RAISE EXCEPTION 'FAIL 11d: identity disclosure accepted without a written purpose';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 11d: identity disclosure requires a written purpose';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 12. CAMPUS LOCATIONS — coordinate verification status is load-bearing
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        UPDATE core.campus_location SET is_active = TRUE WHERE code = 'TEST-UNSURVEYED';
        RAISE EXCEPTION 'FAIL 12a: an unsurveyed location was activated';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 12a: a location cannot go live without verified coordinates';
    END;
    BEGIN
        UPDATE core.campus_location
           SET coordinate_status = 'verified', latitude = 13.0, longitude = 77.0
         WHERE code = 'TEST-UNSURVEYED';
        RAISE EXCEPTION 'FAIL 12b: coordinates marked verified with no recorded source';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 12b: verified coordinates require a source and capture time';
    END;
    BEGIN
        UPDATE core.campus_location SET latitude = 13.0 WHERE code = 'TEST-UNSURVEYED';
        RAISE EXCEPTION 'FAIL 12c: a bare latitude was accepted on an unsurveyed location';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 12c: coordinate_status=required forbids coordinates';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 13. PUBLIC MAP — k = 3 suppression, locations below k omitted entirely
-- ---------------------------------------------------------------------------
-- TEST-A currently holds 2 reports; TEST-B holds 1.  Neither should appear.
DO $$
DECLARE n INTEGER;
BEGIN
    SELECT count(*) INTO n FROM analytics.v_public_safety_map;
    IF n = 0 THEN
        RAISE NOTICE 'PASS 13a: locations below k=3 are absent from the public map';
    ELSE
        RAISE EXCEPTION 'FAIL 13a: % low-count rows leaked to the public map', n;
    END IF;
END $$;

-- Add a third report at TEST-A in the same week so it crosses the threshold.
INSERT INTO core.report (public_ref, report_kind, submission_mode, declared_category_id,
                         location_id, occurred_at, occurred_hour, occurred_dow)
SELECT 'CS-2026-DDDDDD', 'incident', 'anonymous', c.category_id, l.location_id,
       now() - interval '1 hour', 22, 3
  FROM core.report_category c, core.campus_location l
 WHERE c.code = 'TEST_HARASS' AND l.code = 'TEST-A';

DO $$
DECLARE n INTEGER; cnt INTEGER;
BEGIN
    SELECT count(*) INTO n FROM analytics.v_public_safety_map;
    SELECT report_count INTO cnt FROM analytics.v_public_safety_map WHERE location_name = 'Test Location A';
    IF n = 1 AND cnt = 3 THEN
        RAISE NOTICE 'PASS 13b: location appears only once it reaches k=3 (count=%)', cnt;
    ELSE
        RAISE EXCEPTION 'FAIL 13b: expected exactly 1 row with count 3, got % rows / count %', n, cnt;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM analytics.v_public_safety_map WHERE location_name = 'Test Location B') THEN
        RAISE NOTICE 'PASS 13c: the single-report location is still omitted, not shown as "<3"';
    ELSE
        RAISE EXCEPTION 'FAIL 13c: single-report location is visible on the public map';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 14. INTERVENTION IMPACT — controls are mandatory for a DiD claim
-- ---------------------------------------------------------------------------
INSERT INTO intervention.intervention (intervention_id, title, intervention_type, scope,
                                       location_id, status, completed_at, expected_effect)
SELECT 'dddddddd-0000-0000-0000-000000000004', 'Test lighting repair', 'lighting', 'location',
       l.location_id, 'completed', now() - interval '30 days', 'Fewer evening reports'
  FROM core.campus_location l WHERE l.code = 'TEST-A';

DO $$
DECLARE loc_a INTEGER; loc_b INTEGER;
BEGIN
    SELECT location_id INTO loc_a FROM core.campus_location WHERE code = 'TEST-A';
    SELECT location_id INTO loc_b FROM core.campus_location WHERE code = 'TEST-B';

    BEGIN
        INSERT INTO intervention.impact_measurement
            (intervention_id, location_id, baseline_start, baseline_end,
             followup_start, followup_end, window_days, baseline_count, followup_count,
             percent_change, method)
        VALUES ('dddddddd-0000-0000-0000-000000000004', loc_a,
                now() - interval '60 days', now() - interval '30 days',
                now() - interval '30 days', now(), 30, 10, 4, -60.0,
                'difference_in_differences');
        RAISE EXCEPTION 'FAIL 14a: a difference-in-differences result stored without controls';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 14a: DiD requires the control figures it claims';
    END;

    BEGIN
        INSERT INTO intervention.impact_measurement
            (intervention_id, location_id, baseline_start, baseline_end,
             followup_start, followup_end, window_days, baseline_count, followup_count,
             control_location_ids, control_selection_method, method)
        VALUES ('dddddddd-0000-0000-0000-000000000004', loc_a,
                now() - interval '60 days', now() - interval '30 days',
                now() - interval '30 days', now(), 30, 10, 4,
                ARRAY[999999], 'same_zone', 'simple_before_after');
        RAISE EXCEPTION 'FAIL 14b: a non-existent control location was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS 14b: control locations must exist';
    END;

    BEGIN
        INSERT INTO intervention.impact_measurement
            (intervention_id, location_id, baseline_start, baseline_end,
             followup_start, followup_end, window_days, baseline_count, followup_count,
             control_location_ids, control_selection_method, method)
        VALUES ('dddddddd-0000-0000-0000-000000000004', loc_a,
                now() - interval '60 days', now() - interval '30 days',
                now() - interval '30 days', now(), 30, 10, 4,
                ARRAY[loc_a], 'manual', 'simple_before_after');
        RAISE EXCEPTION 'FAIL 14c: the treated location was accepted as its own control';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 14c: the treated location cannot be its own control';
    END;

    -- A well-formed difference-in-differences result.
    INSERT INTO intervention.impact_measurement
        (intervention_id, location_id, baseline_start, baseline_end,
         followup_start, followup_end, window_days, baseline_count, followup_count,
         percent_change, control_location_ids, control_selection_method,
         control_baseline_count, control_followup_count, control_percent_change,
         net_percent_change, method)
    VALUES ('dddddddd-0000-0000-0000-000000000004', loc_a,
            now() - interval '60 days', now() - interval '30 days',
            now() - interval '30 days', now(), 30, 10, 4, -60.0,
            ARRAY[loc_b], 'same_location_type', 20, 19, -5.0, -55.0,
            'difference_in_differences');
    RAISE NOTICE 'PASS 14d: a complete DiD result with controls is accepted';

    BEGIN
        INSERT INTO intervention.impact_measurement
            (intervention_id, location_id, baseline_start, baseline_end,
             followup_start, followup_end, window_days, baseline_count, followup_count, method)
        VALUES ('dddddddd-0000-0000-0000-000000000004', loc_a,
                now() - interval '60 days', now() - interval '30 days',
                now() - interval '45 days', now(), 30, 10, 4, 'simple_before_after');
        RAISE EXCEPTION 'FAIL 14e: overlapping baseline and follow-up windows accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS 14e: baseline and follow-up windows cannot overlap';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 15. CASCADE INTEGRITY — deleting a report removes every fragment of it
-- ---------------------------------------------------------------------------
DO $$
DECLARE leftovers INTEGER;
BEGIN
    DELETE FROM core.report WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001';
    SELECT (SELECT count(*) FROM core.report_narrative WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001')
         + (SELECT count(*) FROM core.case_status_history WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001')
         + (SELECT count(*) FROM core.case_assignment WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001')
         + (SELECT count(*) FROM evidence.evidence_object WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001')
         + (SELECT count(*) FROM core.risk_assessment WHERE report_id = 'aaaaaaaa-0000-0000-0000-000000000001')
         + (SELECT count(*) FROM core.report_link WHERE report_id_a = 'aaaaaaaa-0000-0000-0000-000000000001'
                                                    OR report_id_b = 'aaaaaaaa-0000-0000-0000-000000000001')
      INTO leftovers;
    IF leftovers = 0 THEN
        RAISE NOTICE 'PASS 15a: deleting a report left no orphaned fragments';
    ELSE
        RAISE EXCEPTION 'FAIL 15a: % orphaned rows survived the report deletion', leftovers;
    END IF;

    IF EXISTS (SELECT 1 FROM audit.access_log
                WHERE object_id = 'aaaaaaaa-0000-0000-0000-000000000001') THEN
        RAISE NOTICE 'PASS 15b: the audit trail survived the report deletion';
    ELSE
        RAISE EXCEPTION 'FAIL 15b: audit rows were removed with the report';
    END IF;
END $$;

-- 15c. A user with a live attribution cannot be silently deleted.
DO $$
BEGIN
    BEGIN
        DELETE FROM identity.app_user WHERE user_id = '11111111-1111-1111-1111-111111111111';
        RAISE EXCEPTION 'FAIL 15c: a user with a live attribution was deleted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS 15c: deleting an attributed user is blocked, not silent';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 16. POLICY — a non-prototype origin must cite its source
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        INSERT INTO core.system_policy (policy_key, policy_value, value_type, origin, description)
        VALUES ('test_bogus_claim', '90', 'integer', 'regulatory',
                'Too short to be a real citation.');
        RAISE EXCEPTION 'FAIL 16: a regulatory claim was accepted without a citation';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 16: a non-prototype policy origin must cite its source';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- 17. SYNTHETIC CAMPUS DATA — a demo location cannot masquerade as verified
-- production data, and the schema enforces its own label (Phase 5B)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    -- 17a. is_synthetic = true demands the coordinate_source prefix, even
    -- when every other 'verified' requirement (source present, capture
    -- time present) is otherwise satisfied.
    BEGIN
        INSERT INTO core.campus_location
            (code, name, latitude, longitude, coordinate_status,
             coordinate_source, coordinate_captured_at, is_active, is_synthetic)
        VALUES
            ('TEST-DEMO-BAD', 'Test Demo Bad', 0.001, 0.001, 'verified',
             'a source that does not say DEMO FIXTURE', now(), true, true);
        RAISE EXCEPTION 'FAIL 17a: an unlabelled synthetic row was accepted';
    EXCEPTION WHEN integrity_constraint_violation THEN
        RAISE NOTICE 'PASS 17a: is_synthetic=true demands the DEMO FIXTURE: prefix';
    END;

    -- 17b. Correctly labelled, it is accepted, behaves exactly like a real
    -- verified row (is_active, corroboration-eligible), and is queryable
    -- as synthetic.
    INSERT INTO core.campus_location
        (code, name, latitude, longitude, coordinate_status,
         coordinate_source, coordinate_captured_at, is_active, is_synthetic)
    VALUES
        ('TEST-DEMO-GOOD', 'Test Demo Good', 0.001, 0.001, 'verified',
         'DEMO FIXTURE: schema guarantee check', now(), true, true);

    IF EXISTS (
        SELECT 1 FROM core.campus_location
         WHERE code = 'TEST-DEMO-GOOD' AND is_active AND is_synthetic
    ) THEN
        RAISE NOTICE 'PASS 17b: a correctly labelled demo row is active and flagged synthetic';
    ELSE
        RAISE EXCEPTION 'FAIL 17b: the labelled demo row did not persist as expected';
    END IF;

    -- 17c. An ordinary verified row (no is_synthetic given) defaults to
    -- false — a demo fixture is opt-in, never the default for real data.
    IF EXISTS (SELECT 1 FROM core.campus_location WHERE code = 'TEST-A' AND is_synthetic) THEN
        RAISE EXCEPTION 'FAIL 17c: an ordinary verified row defaulted to is_synthetic=true';
    ELSE
        RAISE NOTICE 'PASS 17c: is_synthetic defaults to false for real data';
    END IF;
END $$;

ROLLBACK;

\echo ''
\echo 'All checks complete. Transaction rolled back; no data retained.'
