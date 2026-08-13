"""Triage end to end: classification, linking, risk, and what a responder sees.

Real PostgreSQL, the real HTTP stack, and the real exported model. The model
fixture registers the actual artifact in `ml.model_version` and installs the
loaded bundle on the app, so every suggestion here comes from scikit-learn
running over a real narrative.

The theme is the one that runs through the whole project: **the machine
suggests, the person decides.** Most of these tests are about what triage is
forbidden from doing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.ml.artifact import load_bundle
from app.models import ModelVersion
from app.models.enums import MlTask, ModelFamily, UserRole

from .conftest import auth

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = PROJECT_ROOT / "ml" / "artifacts" / "tfidf-logreg-1.0.0.joblib"

needs_artifact = pytest.mark.skipif(
    not ARTIFACT.exists(),
    reason="no model artifact; run scripts/export_baseline_model.py",
)


@pytest.fixture()
def real_categories(session):
    """The sixteen production category codes.

    The model's label space *is* `core.report_category.code`, so a suggestion can
    only resolve if those rows exist. Production seeds them from
    `sql/seed_report_categories.sql`; the test database gets them here alongside
    the `TEST_` fixtures every other suite uses. Severity and routing are
    representative, not the institution's final judgement — that decision is
    deferred in the seed file itself.
    """
    from app.models import ReportCategory
    from app.models.enums import ReportKind as Kind

    incidents = [
        "HARASS_VERBAL",
        "HARASS_PHYSICAL",
        "STALKING",
        "HARASS_DIGITAL",
        "INTIMIDATION",
        "RAGGING",
        "VOYEURISM",
        "TRESPASS",
        "OTHER_INCIDENT",
    ]
    concerns = [
        "LIGHTING_POOR",
        "ISOLATED_AREA",
        "CCTV_GAP",
        "BLOCKED_ROUTE",
        "ACCESS_CONTROL",
        "TRANSPORT_SAFETY",
        "OTHER_CONCERN",
    ]
    rows = [
        ReportCategory(
            code=code,
            label=code.replace("_", " ").title(),
            kind=Kind.INCIDENT if code in incidents else Kind.CONCERN,
            routes_to_role=UserRole.ICC if code in incidents else UserRole.SECURITY,
            base_severity=3,
            emergency_eligible=code in incidents,
        )
        for code in incidents + concerns
    ]
    session.add_all(rows)
    session.flush()
    return {row.code: row for row in rows}


@pytest.fixture()
def model(app, session, real_categories):
    """Register the real artifact and install it on the app.

    Mirrors exactly what `scripts/export_baseline_model.py --register` does, so
    the tests exercise the deployed arrangement rather than a parallel one.
    """
    if not ARTIFACT.exists():
        pytest.skip("no model artifact")

    bundle = load_bundle(ARTIFACT)
    row = ModelVersion(
        name=bundle.name,
        task=MlTask.CLASSIFICATION,
        family=ModelFamily.BASELINE,
        version=bundle.version,
        artifact_uri=str(ARTIFACT.relative_to(PROJECT_ROOT)),
        training_rows=bundle.training_rows,
        hyperparameters=bundle.hyperparameters,
        headline_metrics={**bundle.headline_metrics, "data_provenance": bundle.provenance},
        is_active=True,
    )
    session.add(row)
    session.flush()

    app.extensions["model_bundle"] = bundle
    yield row
    app.extensions["model_bundle"] = None


def create(client, users, report_payload, **overrides):
    response = client.post(
        "/api/v1/reports", json=report_payload(**overrides), headers=auth(users["student"])
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def incident(client, users, ref, actor="icc"):
    response = client.get(f"/api/v1/incidents/{ref}", headers=auth(users[actor]))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@needs_artifact
def test_a_submitted_report_gets_a_category_suggestion(
    client, users, session, model, report_payload
):
    created = create(
        client,
        users,
        report_payload,
        narrative="A man followed me from the library to the gate and would not leave me alone.",
    )

    row = session.execute(
        text(
            "SELECT c.predicted_category_id, c.confidence, c.is_current, c.latency_ms "
            "FROM ml.report_classification c JOIN core.report r ON r.report_id = c.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    predicted, confidence, is_current, latency = row

    assert predicted is not None
    assert 0 < float(confidence) <= 1
    assert is_current is True
    assert latency is not None and latency >= 0


@needs_artifact
@pytest.mark.privacy
def test_the_suggestion_never_changes_what_the_student_chose(
    client, users, session, model, report_payload, categories
):
    """The rule this whole layer is built around.

    The same shape as the location trust model: a person's deliberate choice is
    authoritative, and a derived signal describes rather than overrides.
    """
    created = create(
        client,
        users,
        report_payload,
        narrative="The light outside the hostel block has been broken for two weeks now.",
    )

    declared = session.execute(
        text("SELECT declared_category_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()

    assert declared == categories["harassment"].category_id
    assert created["category"]["code"] == "TEST_HARASS"


@needs_artifact
def test_the_full_distribution_is_stored_but_not_served(
    client, users, session, model, report_payload
):
    """Sixteen numbers belong in the database, not on a responder's screen."""
    created = create(client, users, report_payload)

    scores = session.execute(
        text(
            "SELECT c.label_scores FROM ml.report_classification c "
            "JOIN core.report r ON r.report_id = c.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert len(scores) == 16

    body = incident(client, users, created["public_ref"])
    assert "label_scores" not in str(body)


@needs_artifact
def test_a_report_without_a_model_still_succeeds(client, users, session, report_payload):
    """No `model` fixture here — nothing is registered and no bundle is loaded.

    The report must be created anyway. Machine assistance is assistance.
    """
    created = create(client, users, report_payload)

    assert created["public_ref"]
    classified = session.execute(
        text(
            "SELECT count(*) FROM ml.report_classification c "
            "JOIN core.report r ON r.report_id = c.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert classified == 0

    # Risk still scored: it reads the incident, not the narrative.
    risk = session.execute(
        text(
            "SELECT count(*) FROM core.risk_assessment a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert risk == 1


@needs_artifact
def test_a_broken_model_does_not_cost_a_student_their_report(
    client, users, app, model, report_payload
):
    """The failure mode that matters most.

    An estimator that raises on every call must produce a report with no
    suggestion, not a 500 and a lost account of what happened to someone.
    """

    class Exploding:
        classes_ = ["HARASS_VERBAL"]

        def predict_proba(self, _):
            raise RuntimeError("the model is on fire")

    app.extensions["model_bundle"] = type(
        "Bundle", (), {"estimator": Exploding(), "provenance": {}}
    )()

    response = client.post("/api/v1/reports", json=report_payload(), headers=auth(users["student"]))
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


@needs_artifact
def test_every_report_is_risk_scored(client, users, session, model, report_payload):
    created = create(client, users, report_payload)

    row = session.execute(
        text(
            "SELECT a.score, a.band, a.scorer_version, a.is_current, a.trigger_reason "
            "FROM core.risk_assessment a JOIN core.report r ON r.report_id = a.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    score, band, version, is_current, trigger = row

    assert 0 <= float(score) <= 100
    assert band in ("low", "moderate", "high", "critical")
    assert version.startswith("rules-")
    assert is_current is True
    assert trigger == "initial"


@needs_artifact
def test_an_ongoing_emergency_outscores_an_ordinary_report(
    client, users, session, model, report_payload
):
    ordinary = create(client, users, report_payload)
    urgent = create(client, users, report_payload, is_emergency=True, is_ongoing=True)

    def score_for(ref):
        return float(
            session.execute(
                text(
                    "SELECT a.score FROM core.risk_assessment a "
                    "JOIN core.report r ON r.report_id = a.report_id "
                    "WHERE r.public_ref = :ref AND a.is_current"
                ),
                {"ref": ref},
            ).scalar_one()
        )

    assert score_for(urgent["public_ref"]) > score_for(ordinary["public_ref"])


@pytest.mark.privacy
@needs_artifact
def test_the_database_refuses_a_credibility_factor(client, session, model, users, report_payload):
    """The constraint, exercised rather than assumed.

    If the application ever tried to write a reporter-credibility term, this is
    what would stop it. Worth proving against the real database rather than
    trusting the CHECK exists because the migration says so.
    """
    from sqlalchemy.exc import IntegrityError

    created = create(client, users, report_payload)
    report_id = session.execute(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO core.risk_assessment "
                "(report_id, score, band, scorer_version, factors) VALUES "
                "(:rid, 50, 'moderate', 'test', '{\"credibility\": 0.9}'::jsonb)"
            ),
            {"rid": report_id},
        )
    session.rollback()


@pytest.mark.privacy
@needs_artifact
def test_the_database_refuses_a_credibility_weight(client, session, model, users, report_payload):
    """The nested half of the constraint.

    Burying `reporter_relationship` inside `weights` is the plausible way this
    would happen by accident, and the CHECK covers it.
    """
    from sqlalchemy.exc import IntegrityError

    created = create(client, users, report_payload)
    report_id = session.execute(
        text("SELECT report_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO core.risk_assessment "
                "(report_id, score, band, scorer_version, factors) VALUES "
                "(:rid, 50, 'moderate', 'test', "
                '\'{"weights": {"reporter_relationship": 5}}\'::jsonb)'
            ),
            {"rid": report_id},
        )
    session.rollback()


@pytest.mark.privacy
@needs_artifact
def test_an_anonymous_report_is_scored_the_same_as_an_identified_one(
    client, users, session, model, report_payload
):
    """Anonymity must cost nothing.

    Identical incidents, different submission modes, identical score. If
    anonymity lowered a report's position in the queue, the guarantee would be
    worthless in practice however well the database enforced it.
    """
    narrative = "Identical account of an incident, used twice to compare scoring."
    identified = create(client, users, report_payload, narrative=narrative)
    anonymous = create(client, users, report_payload, narrative=narrative, anonymous=True)

    scores = {
        ref: float(
            session.execute(
                text(
                    "SELECT a.score FROM core.risk_assessment a "
                    "JOIN core.report r ON r.report_id = a.report_id "
                    "WHERE r.public_ref = :ref AND a.is_current"
                ),
                {"ref": ref},
            ).scalar_one()
        )
        for ref in (identified["public_ref"], anonymous["public_ref"])
    }
    a, b = scores.values()
    # The location-repeat factor differs by one because the first report now
    # exists, so they are compared within that single point.
    assert abs(a - b) <= 1.0


# ---------------------------------------------------------------------------
# Related reports
# ---------------------------------------------------------------------------


@needs_artifact
def test_two_similar_reports_are_linked(client, users, session, model, report_payload):
    narrative = (
        "A man followed me from the library building all the way to the main gate "
        "and would not stop when I asked him to leave me alone."
    )
    first = create(client, users, report_payload, narrative=narrative)
    second = create(client, users, report_payload, narrative=narrative)

    link = session.execute(
        text(
            "SELECT l.link_type, l.similarity, l.review_state, l.method "
            "FROM core.report_link l "
            "JOIN core.report ra ON ra.report_id = l.report_id_a "
            "JOIN core.report rb ON rb.report_id = l.report_id_b "
            "WHERE ra.public_ref IN (:a, :b) AND rb.public_ref IN (:a, :b)"
        ),
        {"a": first["public_ref"], "b": second["public_ref"]},
    ).one()
    link_type, similarity, review_state, method = link

    assert link_type in ("duplicate", "related", "same_pattern")
    assert float(similarity) >= 0.72
    assert method == "embedding_cosine"
    # Nothing acts on it until a human says so.
    assert review_state == "unreviewed"


@needs_artifact
def test_unrelated_reports_are_not_linked(client, users, session, model, report_payload):
    create(
        client,
        users,
        report_payload,
        narrative="The stairwell light near the sports ground has been out for a fortnight.",
    )
    create(
        client,
        users,
        report_payload,
        narrative="Someone has been sending me threatening messages on my phone all week.",
    )

    count = session.execute(text("SELECT count(*) FROM core.report_link")).scalar_one()
    assert count == 0


@needs_artifact
def test_a_link_is_never_proposed_twice(client, users, session, model, report_payload):
    narrative = "A man followed me from the library to the gate and would not leave."
    create(client, users, report_payload, narrative=narrative)
    create(client, users, report_payload, narrative=narrative)
    create(client, users, report_payload, narrative=narrative)

    rows = session.execute(text("SELECT report_id_a, report_id_b FROM core.report_link")).all()
    assert len(rows) == len(set(rows))
    # And the canonical ordering the CHECK requires.
    for a, b in rows:
        assert str(a) < str(b)


@needs_artifact
@pytest.mark.privacy
def test_a_link_never_widens_access(client, users, session, model, report_payload, categories):
    """The anonymity rule for links.

    An ICC-routed report and a security-routed one with near-identical text will
    be linked. A security officer opening their own report must not learn that
    the ICC one exists — not its reference, not its location.
    """
    narrative = (
        "A man followed me from the library building all the way to the main gate "
        "and would not stop when I asked him to leave me alone."
    )
    icc_side = create(client, users, report_payload, narrative=narrative)
    security_side = create(
        client,
        users,
        report_payload,
        narrative=narrative,
        category_id=categories["lighting"].category_id,
    )

    linked = session.execute(text("SELECT count(*) FROM core.report_link")).scalar_one()
    assert linked >= 1, "the two reports should have been linked in the database"

    body = incident(client, users, security_side["public_ref"], actor="security")
    serialised = str(body)
    assert icc_side["public_ref"] not in serialised
    assert body["triage"]["related"] == []


@needs_artifact
def test_a_responder_sees_a_link_when_entitled_to_both_sides(client, users, model, report_payload):
    narrative = (
        "A man followed me from the library building all the way to the main gate "
        "and would not stop when I asked him to leave me alone."
    )
    first = create(client, users, report_payload, narrative=narrative)
    second = create(client, users, report_payload, narrative=narrative)

    body = incident(client, users, second["public_ref"])
    refs = [item["public_ref"] for item in body["triage"]["related"]]
    assert first["public_ref"] in refs


# ---------------------------------------------------------------------------
# What the responder sees
# ---------------------------------------------------------------------------


@needs_artifact
def test_the_responder_is_told_the_model_is_synthetic_trained(client, users, model, report_payload):
    """Never inferred from silence.

    A suggestion from a synthetically-trained model must not reach a responder
    looking like a finding from one trained on real reports.
    """
    created = create(client, users, report_payload)
    triage = incident(client, users, created["public_ref"])["triage"]

    assert triage["model_trained_on_real_data"] is False
    assert triage["model"].startswith("tfidf-logreg@")


@needs_artifact
def test_risk_factors_are_shown_in_full(client, users, model, report_payload):
    """A number that orders a queue has to be answerable for on screen."""
    created = create(client, users, report_payload, is_emergency=True)
    triage = incident(client, users, created["public_ref"])["triage"]

    assert triage["risk_band"] in ("low", "moderate", "high", "critical")
    factors = triage["risk_factors"]
    assert "contributions" in factors
    assert "weights" in factors
    assert "caveat" in factors
    assert "excluded_by_design" in factors


@needs_artifact
@pytest.mark.privacy
def test_triage_leaks_no_reporter_and_no_embedding(client, users, model, report_payload):
    created = create(client, users, report_payload)
    body = str(incident(client, users, created["public_ref"]))

    assert "user_id" not in body
    assert str(users["student"].user_id) not in body
    assert "embedding" not in body
    assert "student1@test.local" not in body


@needs_artifact
def test_triage_says_whether_the_model_agrees_with_the_reporter(
    client, users, model, report_payload
):
    created = create(client, users, report_payload)
    triage = incident(client, users, created["public_ref"])["triage"]
    assert triage["agrees_with_reporter"] in (True, False)


# ---------------------------------------------------------------------------
# Correction by a human
# ---------------------------------------------------------------------------


@needs_artifact
def test_a_responder_can_record_a_different_category(
    client, users, session, model, report_payload, categories
):
    created = create(client, users, report_payload)
    ref = created["public_ref"]

    response = client.post(
        f"/api/v1/incidents/{ref}/category",
        json={"category_id": categories["lighting"].category_id},
        headers=auth(users["icc"]),
    )
    assert response.status_code == 204

    row = session.execute(
        text(
            "SELECT c.overridden_category_id, c.overridden_by, c.overridden_at "
            "FROM ml.report_classification c JOIN core.report r ON r.report_id = c.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == categories["lighting"].category_id
    assert row[1] == users["icc"].user_id
    assert row[2] is not None


@needs_artifact
@pytest.mark.privacy
def test_an_override_does_not_change_the_students_category(
    client, users, session, model, report_payload, categories
):
    """A responder's judgement is recorded beside the student's choice, not over
    it. Changing the report's own category is case management, with its own
    history trail."""
    created = create(client, users, report_payload)
    client.post(
        f"/api/v1/incidents/{created['public_ref']}/category",
        json={"category_id": categories["lighting"].category_id},
        headers=auth(users["icc"]),
    )

    declared = session.execute(
        text("SELECT declared_category_id FROM core.report WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert declared == categories["harassment"].category_id


@needs_artifact
@pytest.mark.privacy
def test_a_student_cannot_override_a_category(client, users, model, report_payload, categories):
    created = create(client, users, report_payload)
    response = client.post(
        f"/api/v1/incidents/{created['public_ref']}/category",
        json={"category_id": categories["lighting"].category_id},
        headers=auth(users["student"]),
    )
    assert response.status_code == 403


@needs_artifact
def test_a_predicted_code_with_no_category_row_is_handled(
    client, users, session, app, report_payload, real_categories
):
    """Defensive, and reachable: a model may outlive a category being retired.

    The suggestion is recorded with a null category rather than the submission
    failing.
    """
    from app.models import ModelVersion

    bundle = load_bundle(ARTIFACT)
    session.add(
        ModelVersion(
            name=bundle.name,
            task=MlTask.CLASSIFICATION,
            family=ModelFamily.BASELINE,
            version=bundle.version,
            headline_metrics={"data_provenance": bundle.provenance},
            is_active=True,
        )
    )
    session.flush()
    app.extensions["model_bundle"] = bundle
    session.execute(text("DELETE FROM core.report_category WHERE code = 'STALKING'"))

    created = create(
        client,
        users,
        report_payload,
        narrative="A man followed me from the library to the gate and would not leave.",
    )
    assert created["public_ref"]
    app.extensions["model_bundle"] = None


@needs_artifact
def test_an_unknown_category_is_refused(client, users, model, report_payload):
    created = create(client, users, report_payload)
    response = client.post(
        f"/api/v1/incidents/{created['public_ref']}/category",
        json={"category_id": 99999},
        headers=auth(users["icc"]),
    )
    assert response.status_code == 400


@needs_artifact
def test_a_responder_can_confirm_a_link(client, users, session, model, report_payload):
    narrative = (
        "A man followed me from the library building all the way to the main gate "
        "and would not stop when I asked him to leave me alone."
    )
    first = create(client, users, report_payload, narrative=narrative)
    second = create(client, users, report_payload, narrative=narrative)

    body = incident(client, users, second["public_ref"])
    link_id = body["triage"]["related"][0]["link_id"]

    response = client.post(
        f"/api/v1/incidents/{second['public_ref']}/links/{link_id}",
        json={"confirmed": True},
        headers=auth(users["icc"]),
    )
    assert response.status_code == 204

    row = session.execute(
        text("SELECT review_state, reviewed_by FROM core.report_link WHERE link_id = :id"),
        {"id": link_id},
    ).one()
    assert row[0] == "confirmed"
    assert row[1] == users["icc"].user_id
    assert first["public_ref"]


@needs_artifact
def test_a_rejected_link_stops_being_shown(client, users, model, report_payload):
    """A human has said these are not related; the next responder should not have
    to decide again."""
    narrative = (
        "A man followed me from the library building all the way to the main gate "
        "and would not stop when I asked him to leave me alone."
    )
    create(client, users, report_payload, narrative=narrative)
    second = create(client, users, report_payload, narrative=narrative)

    link_id = incident(client, users, second["public_ref"])["triage"]["related"][0]["link_id"]
    client.post(
        f"/api/v1/incidents/{second['public_ref']}/links/{link_id}",
        json={"confirmed": False},
        headers=auth(users["icc"]),
    )

    assert incident(client, users, second["public_ref"])["triage"]["related"] == []


@needs_artifact
@pytest.mark.privacy
def test_a_link_cannot_be_reviewed_from_a_report_it_does_not_touch(
    client, users, model, report_payload
):
    narrative = (
        "A man followed me from the library building all the way to the main gate "
        "and would not stop when I asked him to leave me alone."
    )
    create(client, users, report_payload, narrative=narrative)
    second = create(client, users, report_payload, narrative=narrative)
    unrelated = create(
        client,
        users,
        report_payload,
        narrative="Completely different account about a broken gate latch near parking.",
    )

    link_id = incident(client, users, second["public_ref"])["triage"]["related"][0]["link_id"]

    response = client.post(
        f"/api/v1/incidents/{unrelated['public_ref']}/links/{link_id}",
        json={"confirmed": True},
        headers=auth(users["icc"]),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@needs_artifact
def test_an_override_is_audited(client, users, session, model, report_payload, categories):
    created = create(client, users, report_payload)
    client.post(
        f"/api/v1/incidents/{created['public_ref']}/category",
        json={"category_id": categories["lighting"].category_id},
        headers=auth(users["icc"]),
    )

    outcome = session.execute(
        text(
            "SELECT outcome FROM audit.access_log "
            "WHERE action = 'triage.category_override' AND object_id = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert outcome == "success"


@needs_artifact
@pytest.mark.privacy
def test_the_audit_log_holds_no_narrative_from_triage(
    client, users, session, model, report_payload
):
    created = create(
        client,
        users,
        report_payload,
        narrative="An unmistakable sentence that must never appear in the audit log.",
    )
    incident(client, users, created["public_ref"])

    details = str(list(session.execute(text("SELECT detail FROM audit.access_log")).scalars()))
    assert "unmistakable sentence" not in details
