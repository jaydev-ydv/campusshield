"""The responder plane: queue, detail, destination, dispatch, and who may use it.

Runs against real PostgreSQL through the real HTTP stack, like every other test
here. The authorisation tests in particular are worth doing this way: a policy
unit test proves the function returns False, and an endpoint test proves the
endpoint actually calls it.

`locations["active"]` carries synthetic 0.0/0.0 coordinates from the conftest
fixture, labelled there as such. No test in this file writes a Presidency
University coordinate, and none is needed — what is under test is whether the
system handles mapped and unmapped locations correctly, not where the campus is.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from .conftest import auth, staged_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_report(client, user, payload):
    response = client.post("/api/v1/reports", json=payload, headers=auth(user))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def icc_report(client, users, report_payload, **overrides):
    """A report routed to the ICC (the `harassment` category)."""
    return create_report(client, users["student"], report_payload(**overrides))


def security_report(client, users, categories, report_payload, **overrides):
    """A report routed to security (the `lighting` category)."""
    payload = report_payload(category_id=categories["lighting"].category_id, **overrides)
    return create_report(client, users["student"], payload)


def queue(client, user, **params):
    return client.get("/api/v1/incidents", headers=auth(user), query_string=params)


# ---------------------------------------------------------------------------
# Access control — the queue
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_a_student_cannot_open_the_responder_queue(client, users, report_payload):
    """Not "sees an empty list" — refused.

    A student's own report is reachable through GET /reports/<ref>. The queue is
    other people's reports, and no student has any business holding one.
    """
    icc_report(client, users, report_payload)

    response = queue(client, users["student"])

    assert response.status_code == 403
    assert "public_ref" not in response.get_data(as_text=True)


@pytest.mark.privacy
def test_an_unauthenticated_caller_cannot_open_the_queue(client, users, report_payload):
    icc_report(client, users, report_payload)
    assert client.get("/api/v1/incidents").status_code == 401


def test_an_icc_member_sees_a_report_routed_to_the_icc(client, users, report_payload):
    created = icc_report(client, users, report_payload)

    body = queue(client, users["icc"]).get_json()

    assert [item["public_ref"] for item in body["items"]] == [created["public_ref"]]


@pytest.mark.privacy
def test_a_role_is_not_access(client, users, categories, report_payload):
    """Security does not see ICC-routed reports, and vice versa.

    "Any authority sees everything" is the failure this rule exists to prevent,
    and it is the one that would look like a working feature.
    """
    icc_only = icc_report(client, users, report_payload)
    security_only = security_report(client, users, categories, report_payload)

    icc_refs = [item["public_ref"] for item in queue(client, users["icc"]).get_json()["items"]]
    security_refs = [
        item["public_ref"] for item in queue(client, users["security"]).get_json()["items"]
    ]

    assert icc_only["public_ref"] in icc_refs
    assert icc_only["public_ref"] not in security_refs
    assert security_only["public_ref"] in security_refs
    assert security_only["public_ref"] not in icc_refs


def test_closed_reports_leave_the_queue(client, users, session, report_payload):
    created = icc_report(client, users, report_payload)
    session.execute(
        text("UPDATE core.report SET current_status = 'resolved' WHERE public_ref = :ref"),
        {"ref": created["public_ref"]},
    )
    session.flush()

    refs = [item["public_ref"] for item in queue(client, users["icc"]).get_json()["items"]]
    assert created["public_ref"] not in refs


# ---------------------------------------------------------------------------
# What the queue does and does not carry
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_the_queue_carries_no_reporter_identity(client, users, report_payload):
    icc_report(client, users, report_payload)

    body = queue(client, users["icc"]).get_data(as_text=True)

    assert "user_id" not in body
    assert str(users["student"].user_id) not in body
    assert "student1@test.local" not in body


@pytest.mark.privacy
def test_the_queue_never_exposes_a_storage_path(client, users, session, report_payload):
    token = staged_token(client, users["student"])
    created = icc_report(client, users, report_payload, evidence_tokens=[token])

    stored_path = session.execute(
        text(
            "SELECT e.storage_path FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()

    body = queue(client, users["icc"]).get_data(as_text=True)
    assert stored_path not in body
    assert "evidence/" not in body


def test_the_queue_reports_whether_evidence_exists(client, users, report_payload):
    token = staged_token(client, users["student"])
    with_image = icc_report(client, users, report_payload, evidence_tokens=[token])
    without = icc_report(client, users, report_payload)

    items = {item["public_ref"]: item for item in queue(client, users["icc"]).get_json()["items"]}

    assert items[with_image["public_ref"]]["evidence_count"] == 1
    assert items[without["public_ref"]]["evidence_count"] == 0


def test_emergencies_sort_above_ordinary_reports(client, users, report_payload):
    """Ordering comes from the incident, never from the reporter."""
    icc_report(client, users, report_payload)
    urgent = icc_report(client, users, report_payload, is_emergency=True, is_ongoing=True)

    refs = [item["public_ref"] for item in queue(client, users["icc"]).get_json()["items"]]
    assert refs[0] == urgent["public_ref"]


def test_the_queue_says_whether_mapping_is_available(client, users, session, report_payload):
    """With no verified coordinates there is nothing to draw, and the server says
    so rather than leaving a client to infer it from nulls."""
    icc_report(client, users, report_payload)
    session.execute(
        text(
            "UPDATE core.campus_location SET is_active = false, coordinate_status = 'required', "
            "latitude = NULL, longitude = NULL, coordinate_source = NULL, "
            "coordinate_captured_at = NULL"
        )
    )
    session.expire_all()

    body = queue(client, users["icc"]).get_json()
    assert body["mapping_available"] is False
    assert body["items"][0]["location"]["is_mapped"] is False
    assert body["items"][0]["location"]["latitude"] is None
    # The incident is still listed. An unmapped location does not make a report
    # disappear from the queue.
    assert body["pagination"]["total"] >= 1


# ---------------------------------------------------------------------------
# Incident detail
# ---------------------------------------------------------------------------


def test_a_responder_can_open_an_incident(client, users, report_payload):
    created = icc_report(client, users, report_payload)

    response = client.get(f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"]))

    assert response.status_code == 200
    body = response.get_json()
    assert body["public_ref"] == created["public_ref"]
    assert body["destination"]["name"] == "Test Active Location"


@pytest.mark.privacy
def test_a_student_cannot_open_an_incident_they_reported(client, users, report_payload):
    """Their own report is theirs — through the report endpoint, not this one.

    The responder view carries dispatch state and responder notes, which is not
    material a reporter is given.
    """
    created = icc_report(client, users, report_payload)

    response = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["student"])
    )
    assert response.status_code == 403


@pytest.mark.privacy
def test_a_responder_from_the_wrong_role_gets_404_not_403(client, users, report_payload):
    """404 rather than 403: a 403 confirms the reference is real."""
    created = icc_report(client, users, report_payload)

    response = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["security"])
    )
    assert response.status_code == 404


@pytest.mark.privacy
def test_incident_detail_carries_no_reporter_identity(client, users, report_payload):
    created = icc_report(client, users, report_payload)

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_data(as_text=True)

    assert "user_id" not in body
    assert str(users["student"].user_id) not in body
    assert "student1@test.local" not in body


@pytest.mark.privacy
def test_an_anonymous_incident_says_there_is_nobody_to_contact(client, users, report_payload):
    """Stated positively rather than left as a missing field.

    A responder must know before they set off that there is no one to call.
    """
    created = icc_report(client, users, report_payload, anonymous=True)

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()

    assert body["submission_mode"] == "anonymous"
    assert body["reporter_contactable"] is False
    guidance = body["reporter_contact_guidance"]
    assert "anonymously" in guidance
    assert "do not attempt to work out who reported it" in guidance.lower()


@pytest.mark.privacy
def test_an_anonymous_incident_has_no_attribution_row(client, users, session, report_payload):
    """The guarantee, checked in the database rather than in a response body."""
    created = icc_report(client, users, report_payload, anonymous=True)
    client.get(f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"]))

    count = session.execute(
        text(
            "SELECT count(*) FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert count == 0


def test_admin_sees_the_incident_but_not_the_narrative(client, users, report_payload):
    """The narrative firewall, on the responder plane too."""
    created = icc_report(client, users, report_payload)

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["admin"])
    ).get_json()

    assert body["public_ref"] == created["public_ref"]
    assert body["narrative_available"] is False
    assert body["narrative"] is None
    assert body["narrative_withheld_reason"]


def test_evidence_appears_as_opaque_ids_only(client, users, report_payload):
    token = staged_token(client, users["student"])
    created = icc_report(client, users, report_payload, evidence_tokens=[token])

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()

    assert len(body["evidence"]) == 1
    assert set(body["evidence"][0]) == {"evidence_id"}
    # No URL of any kind, signed or public.
    serialised = str(body)
    assert "http" not in serialised
    assert "storage_path" not in serialised
    assert "filename" not in serialised


# ---------------------------------------------------------------------------
# Destination
# ---------------------------------------------------------------------------


def test_the_destination_is_the_selected_campus_location(client, users, locations, report_payload):
    """Not a coordinate from a photograph. The controlled location, always."""
    created = icc_report(client, users, report_payload)

    destination = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["destination"]

    assert destination["location_id"] == locations["active"].location_id
    assert destination["code"] == "TEST-ACTIVE"


def test_a_real_verified_location_is_not_flagged_synthetic(
    client, users, locations, report_payload
):
    """`locations["active"]` represents what a real, surveyed location looks
    like — `is_synthetic` must say so explicitly (False), everywhere the
    destination and the queue summary carry it."""
    created = icc_report(client, users, report_payload)

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()

    assert body["destination"]["is_synthetic"] is False
    assert body["location"]["is_synthetic"] is False


def test_a_demo_location_is_flagged_synthetic_everywhere_it_appears(
    client, users, session, report_payload
):
    """A `is_synthetic=True` location (Phase 5B) behaves exactly like a
    verified one operationally — mapped, navigable, corroboration-eligible
    — while being unambiguously flagged in the queue, the detail, and the
    destination. It must never be indistinguishable from real data."""
    session.execute(
        text(
            "INSERT INTO core.campus_location "
            "(code, name, latitude, longitude, coordinate_status, coordinate_source, "
            " coordinate_captured_at, is_active, is_synthetic) "
            "VALUES ('TEST-DEMO-INCIDENT', 'Test Demo Incident Location', 0.004, 0.004, "
            "'verified', 'DEMO FIXTURE: test', now(), true, true)"
        )
    )
    session.flush()
    demo_location_id = session.execute(
        text("SELECT location_id FROM core.campus_location WHERE code = 'TEST-DEMO-INCIDENT'")
    ).scalar_one()

    created = icc_report(client, users, report_payload, location_id=demo_location_id)

    queue = client.get("/api/v1/incidents", headers=auth(users["icc"])).get_json()["items"]
    summary = next(item for item in queue if item["public_ref"] == created["public_ref"])
    assert summary["location"]["is_synthetic"] is True
    assert summary["location"]["is_mapped"] is True

    detail = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()
    assert detail["destination"]["is_synthetic"] is True
    assert detail["destination"]["is_mapped"] is True
    assert detail["destination"]["navigable"] is True
    assert detail["destination"]["latitude"] == 0.004


def test_a_verified_location_at_exactly_zero_is_still_mapped(
    client, users, locations, report_payload
):
    """A regression test for a real bug: `destination_for` used to test
    `location.latitude` for truthiness after already confirming it was not
    `None`, and a coordinate of exactly `0.0` — the equator, and also the
    value this project's own synthetic fixtures use, `locations["active"]`
    included — is falsy in Python. That silently reported a genuinely
    verified location as unmapped.
    """
    created = icc_report(client, users, report_payload)

    destination = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["destination"]

    assert destination["is_mapped"] is True
    assert destination["navigable"] is True
    assert destination["latitude"] == 0.0
    assert destination["longitude"] == 0.0


def test_an_unmapped_destination_is_honest_about_it(client, users, session, report_payload):
    created = icc_report(client, users, report_payload)
    session.execute(
        text(
            "UPDATE core.campus_location SET is_active = false, coordinate_status = 'required', "
            "latitude = NULL, longitude = NULL, coordinate_source = NULL, "
            "coordinate_captured_at = NULL WHERE code = 'TEST-ACTIVE'"
        )
    )
    # Raw SQL leaves the identity map holding the pre-update object, and the
    # request below shares this session.
    session.expire_all()

    destination = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["destination"]

    assert destination["is_mapped"] is False
    assert destination["navigable"] is False
    assert destination["latitude"] is None
    # The name still gets a responder to the right building.
    assert destination["name"] == "Test Active Location"


def test_the_destination_carries_the_dispatch_note(client, users, session, report_payload):
    """The last hundred metres. A pin on a building is not the same as knowing
    which entrance to use."""
    session.execute(
        text("UPDATE core.campus_location SET dispatch_note = :note WHERE code = 'TEST-ACTIVE'"),
        {"note": "Enter via the service gate; lift to level 2."},
    )
    session.expire_all()
    created = icc_report(client, users, report_payload, location_hint="near the rear stairwell")

    destination = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["destination"]

    assert destination["dispatch_note"] == "Enter via the service gate; lift to level 2."
    assert destination["location_hint"] == "near the rear stairwell"


def test_a_provisional_coordinate_is_not_navigable(client, users, session, report_payload):
    """A point nobody has stood at must not route a responder during an emergency."""
    created = icc_report(client, users, report_payload)
    session.execute(
        text(
            "UPDATE core.campus_location SET is_active = false, "
            "coordinate_status = 'provisional' WHERE code = 'TEST-ACTIVE'"
        )
    )
    session.expire_all()

    destination = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["destination"]

    assert destination["navigable"] is False
    assert destination["latitude"] is None


# ---------------------------------------------------------------------------
# Location signal
# ---------------------------------------------------------------------------


def test_every_report_records_a_location_resolution(client, users, session, report_payload):
    created = icc_report(client, users, report_payload)

    resolution = session.execute(
        text(
            "SELECT d.resolution FROM core.report_location_detail d "
            "JOIN core.report r ON r.report_id = d.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    # The fixture location is verified (synthetically), and no photo carried GPS.
    assert resolution == "approximate"


def test_the_signal_never_moves_the_incident(client, users, session, report_payload):
    """A photograph with far-away GPS changes the resolution, never the location."""
    from .conftest import upload
    from .test_exif_location import make_photo

    response = upload(client, users["student"], make_photo(latitude=51.5074, longitude=-0.1278))
    token = response.get_json()["upload_token"]
    created = icc_report(client, users, report_payload, evidence_tokens=[token])

    row = session.execute(
        text(
            "SELECT r.location_id, d.resolution, d.distance_m "
            "FROM core.report r JOIN core.report_location_detail d "
            "ON d.report_id = r.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    location_id, resolution, distance_m = row

    assert resolution == "conflicting"
    assert distance_m > 1000
    # The selected location is untouched.
    assert location_id == 1 or location_id > 0
    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()
    assert body["location"]["code"] == "TEST-ACTIVE"
    assert body["destination"]["code"] == "TEST-ACTIVE"


def test_a_nearby_signal_corroborates_the_selected_location(client, users, session, report_payload):
    """The other half of the four-state resolver: a photo whose GPS agrees
    with the selected location, within the corroboration radius (150 m
    default). `test_every_report_records_a_location_resolution` covers no
    signal at all (`approximate`) and `test_the_signal_never_moves_the_
    incident` covers a signal thousands of kilometres away (`conflicting`);
    neither exercises the agreement path this resolver exists to recognise.
    """
    from .conftest import upload
    from .test_exif_location import make_photo

    # locations["active"] (TEST-ACTIVE) sits at exactly (0.0, 0.0). ~0.0003
    # degrees of latitude and longitude at the equator is roughly 33 m each,
    # combining to a straight-line distance well inside the 150 m radius —
    # comfortably corroborated without being suspiciously exact.
    token = upload(
        client, users["student"], make_photo(latitude=0.0003, longitude=0.0003)
    ).get_json()["upload_token"]
    created = icc_report(client, users, report_payload, evidence_tokens=[token])

    row = session.execute(
        text(
            "SELECT r.location_id, d.resolution, d.distance_m "
            "FROM core.report r JOIN core.report_location_detail d "
            "ON d.report_id = r.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    location_id, resolution, distance_m = row

    assert resolution == "corroborated"
    assert 0 < distance_m < 150

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()
    # Corroboration never becomes the incident location — it stays the
    # student's selection either way.
    assert body["location"]["code"] == "TEST-ACTIVE"
    assert body["destination"]["code"] == "TEST-ACTIVE"
    assert body["location_signal"]["resolution"] == "corroborated"
    assert location_id > 0


@pytest.mark.privacy
def test_the_responder_is_not_shown_the_raw_coordinate(client, users, report_payload):
    """A conflict is surfaced. The reporter's coordinate is not.

    The responder needs to know a photograph's location data disagrees and
    roughly by how far. The coordinate itself is the reporter's position and has
    no operational use.
    """
    from .conftest import upload
    from .test_exif_location import make_photo

    token = upload(
        client, users["student"], make_photo(latitude=51.5074, longitude=-0.1278)
    ).get_json()["upload_token"]
    created = icc_report(client, users, report_payload, evidence_tokens=[token])

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()

    signal = body["location_signal"]
    assert signal["resolution"] == "conflicting"
    assert signal["distance_m"] > 1000
    assert set(signal) == {"resolution", "source", "distance_m", "signal_captured_at", "note"}
    assert "51.5" not in str(body)
    assert "0.12" not in str(body)


def test_a_photo_without_gps_is_unremarkable(client, users, report_payload):
    """Absence is the common case and must not read as a problem."""
    token = staged_token(client, users["student"])
    created = icc_report(client, users, report_payload, evidence_tokens=[token])

    body = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()

    assert body["location_signal"]["resolution"] == "approximate"
    assert body["location_signal"]["note"] is None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch_url(ref: str) -> str:
    return f"/api/v1/incidents/{ref}/dispatch"


def emergency(client, users, report_payload, **overrides):
    """A dispatchable report.

    `trg_dispatch_requires_emergency` permits a dispatch row only against an
    emergency report, so every dispatch test starts from one.
    """
    return icc_report(
        client, users, report_payload, is_emergency=True, is_ongoing=True, **overrides
    )


def test_a_responder_can_raise_a_dispatch(client, users, report_payload):
    created = emergency(client, users, report_payload)

    response = client.post(dispatch_url(created["public_ref"]), headers=auth(users["icc"]))

    assert response.status_code == 201
    assert response.get_json()["state"] == "pending"


@pytest.mark.privacy
def test_a_student_cannot_raise_a_dispatch(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = client.post(dispatch_url(created["public_ref"]), headers=auth(users["student"]))
    assert response.status_code == 403


@pytest.mark.privacy
def test_a_responder_from_the_wrong_role_cannot_dispatch(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = client.post(dispatch_url(created["public_ref"]), headers=auth(users["security"]))
    assert response.status_code == 404


def test_the_full_responder_sequence(client, users, report_payload):
    """Incident → dispatch → acknowledge → en route → on scene → closed."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])

    client.post(dispatch_url(ref), headers=headers)

    for state in ("acknowledged", "dispatched", "on_scene", "closed"):
        response = client.post(f"{dispatch_url(ref)}/state", json={"state": state}, headers=headers)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["state"] == state

    body = response.get_json()
    assert body["acknowledged_at"] is not None
    assert body["dispatched_at"] is not None
    assert body["on_scene_at"] is not None
    assert body["closed_at"] is not None


def test_a_dispatch_cannot_skip_states(client, users, report_payload):
    """A responder cannot arrive without setting off."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    client.post(dispatch_url(ref), headers=auth(users["icc"]))

    response = client.post(
        f"{dispatch_url(ref)}/state", json={"state": "on_scene"}, headers=auth(users["icc"])
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["details"]["allowed"] == ["acknowledged", "stood_down"]


def test_a_dispatch_cannot_move_backwards(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)
    client.post(f"{dispatch_url(ref)}/state", json={"state": "acknowledged"}, headers=headers)

    response = client.post(f"{dispatch_url(ref)}/state", json={"state": "pending"}, headers=headers)
    assert response.status_code == 409


def test_a_closed_dispatch_is_terminal(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)
    for state in ("acknowledged", "dispatched", "on_scene", "closed"):
        client.post(f"{dispatch_url(ref)}/state", json={"state": state}, headers=headers)

    response = client.post(
        f"{dispatch_url(ref)}/state", json={"state": "acknowledged"}, headers=headers
    )
    assert response.status_code == 409


def test_a_dispatch_can_be_stood_down_before_arrival(client, users, report_payload):
    """Stood down is not a step towards closed — it records that nobody arrived."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)
    client.post(f"{dispatch_url(ref)}/state", json={"state": "acknowledged"}, headers=headers)

    response = client.post(
        f"{dispatch_url(ref)}/state", json={"state": "stood_down"}, headers=headers
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["state"] == "stood_down"
    assert body["on_scene_at"] is None


def test_two_open_dispatches_are_refused(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    client.post(dispatch_url(ref), headers=auth(users["icc"]))

    response = client.post(dispatch_url(ref), headers=auth(users["icc"]))
    assert response.status_code == 409


def test_a_non_emergency_report_cannot_be_dispatched(client, users, report_payload):
    """A database rule, surfaced as a stated reason rather than a 500.

    `trg_dispatch_requires_emergency` would refuse the insert anyway; the service
    checks first so the responder is told why.
    """
    created = icc_report(client, users, report_payload)

    response = client.post(dispatch_url(created["public_ref"]), headers=auth(users["icc"]))

    assert response.status_code == 409
    assert "emergency" in response.get_json()["error"]["message"].lower()


def test_advancing_without_a_dispatch_is_a_404(client, users, report_payload):
    created = emergency(client, users, report_payload)
    response = client.post(
        f"{dispatch_url(created['public_ref'])}/state",
        json={"state": "acknowledged"},
        headers=auth(users["icc"]),
    )
    assert response.status_code == 404


def test_an_unknown_state_is_rejected(client, users, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    client.post(dispatch_url(ref), headers=auth(users["icc"]))

    response = client.post(
        f"{dispatch_url(ref)}/state", json={"state": "teleported"}, headers=auth(users["icc"])
    )
    assert response.status_code == 400


@pytest.mark.privacy
def test_a_dispatch_never_names_the_responder_in_a_response(client, users, report_payload):
    """`acknowledged_by` is recorded, and is not serialised.

    Which colleague took a call is internal, and emitting it would put a user id
    in a response body.
    """
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)

    response = client.post(
        f"{dispatch_url(ref)}/state", json={"state": "acknowledged"}, headers=headers
    )

    body = response.get_data(as_text=True)
    assert "acknowledged_by" not in body
    assert str(users["icc"].user_id) not in body


def test_the_acknowledging_responder_is_recorded_in_the_database(
    client, users, session, report_payload
):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)
    client.post(f"{dispatch_url(ref)}/state", json={"state": "acknowledged"}, headers=headers)

    acknowledged_by = session.execute(
        text(
            "SELECT d.acknowledged_by FROM core.emergency_dispatch d "
            "JOIN core.report r ON r.report_id = d.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).scalar_one()
    assert acknowledged_by == users["icc"].user_id


def test_a_dispatch_note_is_stored_as_internal(client, users, session, report_payload):
    """`responder_note` is internal; `public_note` is what would ever reach a
    reporter, and advancing a dispatch never writes it."""
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)

    client.post(
        f"{dispatch_url(ref)}/state",
        json={"state": "acknowledged", "note": "Two officers attending."},
        headers=headers,
    )

    row = session.execute(
        text(
            "SELECT d.responder_note, d.public_note FROM core.emergency_dispatch d "
            "JOIN core.report r ON r.report_id = d.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).one()
    assert row[0] == "Two officers attending."
    assert row[1] is None


def test_dispatch_state_appears_in_the_queue(client, users, report_payload):
    created = emergency(client, users, report_payload)
    client.post(dispatch_url(created["public_ref"]), headers=auth(users["icc"]))

    items = {item["public_ref"]: item for item in queue(client, users["icc"]).get_json()["items"]}
    assert items[created["public_ref"]]["dispatch"]["state"] == "pending"


def test_an_incident_without_a_dispatch_says_so(client, users, report_payload):
    created = icc_report(client, users, report_payload)
    items = {item["public_ref"]: item for item in queue(client, users["icc"]).get_json()["items"]}
    assert items[created["public_ref"]]["dispatch"] is None


# ---------------------------------------------------------------------------
# Evidence, from the responder side
# ---------------------------------------------------------------------------


def test_a_responder_can_view_evidence_on_a_routed_incident(client, users, report_payload):
    token = staged_token(client, users["student"])
    created = icc_report(client, users, report_payload, evidence_tokens=[token])
    evidence_id = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["evidence"][0]["evidence_id"]

    response = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["icc"]))

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.privacy
def test_a_responder_from_the_wrong_role_cannot_view_evidence(client, users, report_payload):
    """Direct API access, with the UI bypassed entirely."""
    token = staged_token(client, users["student"])
    created = icc_report(client, users, report_payload, evidence_tokens=[token])
    evidence_id = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["evidence"][0]["evidence_id"]

    response = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["security"]))
    assert response.status_code == 404


@pytest.mark.privacy
def test_evidence_served_to_a_responder_still_carries_no_metadata(client, users, report_payload):
    """The 4B-1 guarantee, checked again on the path a responder actually uses."""
    from PIL import Image

    from .conftest import upload
    from .test_exif_location import make_photo

    original = make_photo(latitude=51.5074, longitude=-0.1278)
    token = upload(client, users["student"], original).get_json()["upload_token"]
    created = icc_report(client, users, report_payload, evidence_tokens=[token])
    evidence_id = client.get(
        f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"])
    ).get_json()["evidence"][0]["evidence_id"]

    served = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["icc"])).data

    import io as _io

    before = Image.open(_io.BytesIO(original))
    after = Image.open(_io.BytesIO(served))

    assert dict(before.getexif().get_ifd(0x8825))  # the original really had GPS
    assert len(dict(after.getexif())) == 0
    assert not dict(after.getexif().get_ifd(0x8825))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_opening_the_queue_is_audited(client, users, session, report_payload):
    icc_report(client, users, report_payload)
    queue(client, users["icc"])

    count = session.execute(
        text("SELECT count(*) FROM audit.access_log WHERE action = 'incident.queue_view'")
    ).scalar_one()
    assert count >= 1


def test_opening_an_incident_is_audited(client, users, session, report_payload):
    created = icc_report(client, users, report_payload)
    client.get(f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"]))

    row = session.execute(
        text(
            "SELECT actor_role, outcome FROM audit.access_log "
            "WHERE action = 'incident.view' AND object_id = :ref"
        ),
        {"ref": created["public_ref"]},
    ).one()
    assert row[0] == "icc"
    assert row[1] == "success"


def test_dispatch_actions_are_audited(client, users, session, report_payload):
    created = emergency(client, users, report_payload)
    ref = created["public_ref"]
    headers = auth(users["icc"])
    client.post(dispatch_url(ref), headers=headers)
    client.post(f"{dispatch_url(ref)}/state", json={"state": "acknowledged"}, headers=headers)

    actions = set(
        session.execute(
            text("SELECT action FROM audit.access_log WHERE object_id = :ref"), {"ref": ref}
        ).scalars()
    )
    assert {"dispatch.raised", "dispatch.acknowledged"} <= actions


@pytest.mark.privacy
def test_the_audit_log_holds_no_narrative_or_path(client, users, session, report_payload):
    token = staged_token(client, users["student"])
    created = icc_report(
        client,
        users,
        report_payload,
        evidence_tokens=[token],
        narrative="A distinctive sentence that must not reach the audit log at all.",
    )
    client.get(f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["icc"]))

    details = str(list(session.execute(text("SELECT detail FROM audit.access_log")).scalars()))
    assert "distinctive sentence" not in details
    assert "evidence/" not in details


@pytest.mark.privacy
def test_a_refused_queue_attempt_is_audited(client, users, session, report_payload):
    """A probe is the access most worth recording.

    The evidence route has audited denials since 4B-1; the responder routes do
    the same, so a student repeatedly trying the queue leaves a trail rather than
    silently bouncing off a 403.
    """
    icc_report(client, users, report_payload)

    queue(client, users["student"])

    row = session.execute(
        text(
            "SELECT actor_role, outcome FROM audit.access_log "
            "WHERE action = 'incident.queue_view' AND outcome = 'denied'"
        )
    ).one()
    assert row[0] == "student"


@pytest.mark.privacy
def test_a_refused_incident_view_is_audited(client, users, session, report_payload):
    created = icc_report(client, users, report_payload)

    client.get(f"/api/v1/incidents/{created['public_ref']}", headers=auth(users["security"]))

    outcome = session.execute(
        text(
            "SELECT outcome FROM audit.access_log "
            "WHERE action = 'incident.view' AND object_id = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert outcome == "denied"


def test_a_refused_dispatch_is_audited(client, users, session, report_payload):
    created = icc_report(client, users, report_payload)  # not an emergency

    response = client.post(dispatch_url(created["public_ref"]), headers=auth(users["icc"]))
    assert response.status_code == 409

    outcome = session.execute(
        text(
            "SELECT outcome FROM audit.access_log "
            "WHERE action = 'dispatch.raised' AND object_id = :ref"
        ),
        {"ref": created["public_ref"]},
    ).scalar_one()
    assert outcome == "denied"
