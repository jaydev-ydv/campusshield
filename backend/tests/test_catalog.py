"""Campus location and report category retrieval."""

from __future__ import annotations

import pytest

from .conftest import auth


def test_locations_require_authentication(client):
    response = client.get("/api/v1/locations")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "UNAUTHENTICATED"


def test_locations_returns_active_only(client, users, locations):
    """An un-surveyed location must never be offered as a choice.

    It has no coordinates, so a report filed against it could never appear on a
    map or feed hotspot detection.
    """
    response = client.get("/api/v1/locations", headers=auth(users["student"]))
    assert response.status_code == 200

    codes = {item["code"] for item in response.get_json()["items"]}
    assert "TEST-ACTIVE" in codes
    assert "TEST-UNSURVEYED" not in codes


def test_locations_can_filter_by_type(client, users, locations):
    response = client.get("/api/v1/locations?location_type=parking", headers=auth(users["student"]))
    codes = {item["code"] for item in response.get_json()["items"]}
    assert codes == {"TEST-ACTIVE-2"}


def test_locations_carry_is_synthetic_false_for_a_real_fixture(client, users, locations):
    """`locations["active"]` is `coordinate_status='verified'` but not a demo
    fixture — `is_synthetic` must say so explicitly, not merely omit it."""
    items = client.get("/api/v1/locations", headers=auth(users["student"])).get_json()["items"]
    active = next(item for item in items if item["code"] == "TEST-ACTIVE")
    assert active["is_synthetic"] is False


def test_a_demo_location_is_flagged_as_synthetic(client, users, session):
    """A `is_synthetic=True` row (Phase 5B) must be visibly flagged through
    the same endpoint a student uses to choose where to report — never a
    silent, indistinguishable entry in the list."""
    from sqlalchemy import text

    session.execute(
        text(
            "INSERT INTO core.campus_location "
            "(code, name, latitude, longitude, coordinate_status, coordinate_source, "
            " coordinate_captured_at, is_active, is_synthetic) "
            "VALUES ('TEST-DEMO', 'Test Demo Location', 0.002, 0.002, 'verified', "
            "'DEMO FIXTURE: test', now(), true, true)"
        )
    )
    session.flush()

    items = client.get("/api/v1/locations", headers=auth(users["student"])).get_json()["items"]
    demo = next(item for item in items if item["code"] == "TEST-DEMO")
    assert demo["is_synthetic"] is True


@pytest.mark.privacy
def test_locations_do_not_expose_surveillance_gaps(client, users, locations):
    """`has_cctv` and `has_lighting` stay server-side.

    Published as an open endpoint, "no camera here, unlit after dark" is a map of
    where not to be seen. They remain available to risk scoring internally.
    """
    item = client.get("/api/v1/locations", headers=auth(users["student"])).get_json()["items"][0]
    assert "has_cctv" not in item
    assert "has_lighting" not in item


def test_categories_returns_active_only(client, users, categories):
    response = client.get("/api/v1/categories", headers=auth(users["student"]))
    assert response.status_code == 200

    codes = {item["code"] for item in response.get_json()["items"]}
    assert {"TEST_HARASS", "TEST_LIGHTING"} <= codes
    assert "TEST_RETIRED" not in codes


def test_categories_filter_by_kind(client, users, categories):
    response = client.get("/api/v1/categories?kind=concern", headers=auth(users["student"]))
    codes = {item["code"] for item in response.get_json()["items"]}
    assert codes == {"TEST_LIGHTING"}


def test_categories_reject_invalid_kind(client, users, categories):
    response = client.get("/api/v1/categories?kind=nonsense", headers=auth(users["student"]))
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.privacy
def test_categories_do_not_expose_routing(client, users, categories):
    """A student choosing a category should not be choosing an audience, nor be
    able to infer how seriously each option is weighted."""
    item = client.get("/api/v1/categories", headers=auth(users["student"])).get_json()["items"][0]
    assert "routes_to_role" not in item
    assert "base_severity" not in item
