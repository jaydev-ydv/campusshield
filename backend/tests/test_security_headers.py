"""Baseline security headers, applied globally (Phase 4G).

A credentialed, browser-facing API needs defensive headers on every
response, not only the evidence endpoint's own carefully-built one. These
tests exercise the real `after_request` hook through the real Flask test
client, against ordinary JSON routes and against the evidence route whose
own, stricter headers must survive unclobbered.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from .conftest import auth, make_image, upload


def test_baseline_headers_are_present_on_an_ordinary_response(client):
    response = client.get("/api/v1/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "max-age=" in response.headers["Strict-Transport-Security"]


def test_baseline_headers_are_present_on_an_authenticated_response(client, users):
    response = client.get("/api/v1/auth/me", headers=auth(users["student"]))

    assert response.headers["X-Frame-Options"] == "DENY"


def test_baseline_headers_are_present_on_an_error_response(client, users):
    response = client.get(f"/api/v1/evidence/{uuid.uuid4()}", headers=auth(users["student"]))

    assert response.status_code == 404
    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_evidence_endpoints_own_stricter_csp_is_not_overridden(
    client, users, session, report_payload
):
    """The evidence route sets `Content-Security-Policy: default-src 'none';
    sandbox` — narrower than the global default. The global hook must use
    `setdefault`, not overwrite: this is the regression test that a future
    edit clobbering that ordering would actually catch."""
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    ref = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token]),
        headers=auth(users["student"]),
    ).get_json()["public_ref"]
    evidence_id = session.execute(
        text(
            "SELECT e.evidence_id FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    ).scalar_one()

    response = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["student"]))

    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
