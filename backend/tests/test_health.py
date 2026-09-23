"""Health and database connectivity."""

from __future__ import annotations

from pathlib import Path


def _head_revision() -> str:
    """The newest migration revision on disk.

    Derived rather than hard-coded: migrations are numbered sequentially, so the
    highest filename prefix is the head.
    """
    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    revisions = sorted(p.name.split("_", 1)[0] for p in versions.glob("[0-9]*_*.py"))
    return revisions[-1]


def test_health_endpoint_is_public(client):
    """Liveness must not require authentication.

    An orchestrator probing this endpoint has no credentials, and a health check
    that 401s reads as a dead process.
    """
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_api_health_endpoint(client):
    """GET /api/health minimal health check returning status ok and message."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "message": "CampusShield API is running"}


def test_health_endpoint_returns_request_id(client):
    response = client.get("/api/v1/health")
    assert response.headers.get("X-Request-ID")


def test_health_echoes_client_request_id(client):
    """A well-formed client id is honoured so a trace can span frontend and API."""
    response = client.get("/api/v1/health", headers={"X-Request-ID": "frontend-trace-0001"})
    assert response.headers["X-Request-ID"] == "frontend-trace-0001"


def test_health_rejects_malformed_request_id(client):
    """A malformed id is replaced, not echoed — an unvalidated echo into log
    files is how log injection works."""
    hostile = "id with spaces and <script>"
    response = client.get("/api/v1/health", headers={"X-Request-ID": hostile})
    assert response.headers["X-Request-ID"] != hostile


def test_database_health_reports_connection_and_schema(client):
    response = client.get("/api/v1/health/db")
    assert response.status_code == 200
    body = response.get_json()

    assert body["connected"] is True
    assert body["ready"] is True
    # The head revision, read from Alembic rather than hard-coded, so adding a
    # migration does not require editing this test to keep it honest.
    assert body["migration_revision"] == _head_revision()
    for schema in ("identity", "core", "evidence", "ml", "analytics", "audit"):
        assert schema in body["schemas_present"]
    assert "schemas_missing" not in body


def test_database_health_does_not_leak_the_connection_string(client):
    """A driver error can carry host, port and username from the DSN."""
    body = client.get("/api/v1/health/db").get_json()
    serialised = str(body)
    assert "postgresql" not in serialised
    assert "password" not in serialised.lower()


def test_index_lists_endpoints(client):
    body = client.get("/").get_json()
    assert body["service"] == "CampusShield API"
    assert any("/reports" in endpoint for endpoint in body["endpoints"])
