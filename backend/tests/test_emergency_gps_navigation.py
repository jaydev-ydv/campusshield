"""Emergency SOS real GPS capture, accuracy persistence, and responder navigation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.enums import LocationSignalSource

from .conftest import auth


def _sos(client, user, **body):
    return client.post("/api/v1/reports/emergency", json=body, headers=auth(user))


def test_sos_with_gps_coordinates_and_accuracy(client, users, session):
    """Submitting emergency SOS with valid GPS coordinates, accuracy, and timestamp."""
    now_iso = datetime.now(timezone.utc).isoformat()
    response = _sos(
        client,
        users["student"],
        latitude=13.134567,
        longitude=77.567890,
        accuracy=15.5,
        captured_at=now_iso,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["is_emergency"] is True
    assert body["public_ref"].startswith("CS-")
    assert body["location_status"] == "gps_captured"
    assert "coordinates" in body
    assert round(body["coordinates"]["latitude"], 6) == 13.134567
    assert round(body["coordinates"]["longitude"], 6) == 77.567890
    assert body["coordinates"]["accuracy_m"] == 15.5
    assert body["coordinates"]["source"] == "device_gps"

    # Verify database persistence in core.report_location_detail
    detail_row = session.execute(
        text(
            "SELECT d.source, d.signal_latitude, d.signal_longitude, d.signal_accuracy_m "
            "FROM core.report_location_detail d "
            "JOIN core.report r ON r.report_id = d.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": body["public_ref"]},
    ).fetchone()

    assert detail_row is not None
    assert detail_row.source == LocationSignalSource.DEVICE_GPS.value
    assert float(detail_row.signal_latitude) == 13.134567
    assert float(detail_row.signal_longitude) == 77.567890
    assert float(detail_row.signal_accuracy_m) == 15.5


def test_sos_missing_gps_still_succeeds(client, users, session):
    """Missing GPS never blocks an emergency submission."""
    response = _sos(client, users["student"])
    assert response.status_code == 201
    body = response.get_json()
    assert body["is_emergency"] is True
    assert body["location_status"] == "unavailable"

    # Detail row has location_default and no signal coordinates
    detail_row = session.execute(
        text(
            "SELECT d.source, d.signal_latitude, d.signal_longitude, d.signal_accuracy_m "
            "FROM core.report_location_detail d "
            "JOIN core.report r ON r.report_id = d.report_id "
            "WHERE r.public_ref = :ref"
        ),
        {"ref": body["public_ref"]},
    ).fetchone()

    assert detail_row is not None
    assert detail_row.source == LocationSignalSource.LOCATION_DEFAULT.value
    assert detail_row.signal_latitude is None
    assert detail_row.signal_longitude is None
    assert detail_row.signal_accuracy_m is None


def test_sos_rejects_unpaired_coordinates(client, users):
    """Latitude without longitude or longitude without latitude is rejected."""
    resp1 = _sos(client, users["student"], latitude=12.34)
    assert resp1.status_code == 400

    resp2 = _sos(client, users["student"], longitude=77.12)
    assert resp2.status_code == 400


def test_sos_rejects_out_of_range_coordinates(client, users):
    """Latitude must be -90..90 and longitude -180..180."""
    resp1 = _sos(client, users["student"], latitude=95.0, longitude=77.0)
    assert resp1.status_code == 400

    resp2 = _sos(client, users["student"], latitude=12.0, longitude=185.0)
    assert resp2.status_code == 400


def test_sos_rejects_negative_accuracy(client, users):
    """Accuracy in metres cannot be negative."""
    resp = _sos(client, users["student"], latitude=12.0, longitude=77.0, accuracy=-5.0)
    assert resp.status_code == 400


def test_sos_rejects_non_finite_coordinates(client, users):
    """NaN or Inf are rejected."""
    # JSON standard doesn't have NaN, but Python clients/malformed requests may pass string or float
    resp = client.post(
        "/api/v1/reports/emergency",
        json={"latitude": 1e999, "longitude": 77.0},
        headers=auth(users["student"]),
    )
    assert resp.status_code == 400


def test_sos_rejects_future_capture_timestamp(client, users):
    """Timestamps far into the future are rejected."""
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    resp = _sos(
        client,
        users["student"],
        latitude=12.0,
        longitude=77.0,
        captured_at=future,
    )
    assert resp.status_code == 400


def test_responder_incident_destination_has_gps_navigation(client, users):
    """Security responders receive navigable GPS destination for emergency SOS reports."""
    resp = _sos(
        client,
        users["student"],
        latitude=13.134567,
        longitude=77.567890,
        accuracy=8.0,
    )
    ref = resp.get_json()["public_ref"]

    # Security responder opens incident
    sec_resp = client.get(f"/api/v1/incidents/{ref}", headers=auth(users["security"]))
    assert sec_resp.status_code == 200
    incident = sec_resp.get_json()

    # Destination reflects device GPS
    dest = incident["destination"]
    assert dest["is_mapped"] is True
    assert dest["navigable"] is True
    assert dest["is_gps"] is True
    assert round(dest["latitude"], 6) == 13.134567
    assert round(dest["longitude"], 6) == 77.567890
    assert dest["accuracy_m"] == 8.0

    # location_signal has device_gps coordinates
    signal = incident["location_signal"]
    assert signal is not None
    assert signal["source"] == "device_gps"
    assert round(signal["signal_latitude"], 6) == 13.134567
    assert round(signal["signal_longitude"], 6) == 77.567890
    assert signal["accuracy_m"] == 8.0


def test_responder_incident_queue_surfaces_gps_coordinates(client, users):
    """Incident queue item carries GPS coordinates in location_signal."""
    resp = _sos(
        client,
        users["student"],
        latitude=13.134567,
        longitude=77.567890,
        accuracy=12.0,
    )
    ref = resp.get_json()["public_ref"]

    queue_resp = client.get("/api/v1/incidents", headers=auth(users["security"]))
    assert queue_resp.status_code == 200
    items = queue_resp.get_json()["items"]
    matched = next((item for item in items if item["public_ref"] == ref), None)
    assert matched is not None
    assert matched["is_emergency"] is True
    assert matched["location_signal"]["source"] == "device_gps"
    assert round(matched["location_signal"]["signal_latitude"], 6) == 13.134567
    assert round(matched["location_signal"]["signal_longitude"], 6) == 77.567890
    assert matched["location_signal"]["accuracy_m"] == 12.0
