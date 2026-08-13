"""Health and readiness endpoints.  Public — no authentication."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..schemas.responses import serialize_database_health
from .dependencies import health_service

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    """Liveness. Touches nothing external.

    A liveness probe that queries the database restarts a healthy application
    whenever the database hiccups, turning a brief dependency outage into a
    second, self-inflicted one.
    """
    return jsonify(health_service().liveness()), 200


@bp.get("/health/db")
def health_db():
    """Readiness: connectivity, server version, migration revision, schemas.

    Returns 503 when the database is unreachable or the expected schemas are
    absent. A live connection to an un-migrated database looks perfectly healthy
    right up until the first insert fails, so the revision is reported too.
    """
    result = health_service().database()
    status = 200 if result.ready else 503
    return jsonify(serialize_database_health(result)), status
