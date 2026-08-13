"""Liveness and readiness."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..repositories.health_repository import EXPECTED_SCHEMAS, HealthRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DatabaseHealth:
    connected: bool
    server_version: str | None = None
    migration_revision: str | None = None
    schemas_present: list[str] = field(default_factory=list)
    schemas_missing: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.connected and not self.schemas_missing


class HealthService:
    def __init__(self, *, health: HealthRepository) -> None:
        self._health = health

    def liveness(self) -> dict[str, str]:
        """Is the process up?  Deliberately touches nothing external.

        A liveness probe that queries the database restarts a healthy
        application every time the database hiccups, which turns a brief
        dependency outage into an outage of its own.
        """
        return {"status": "ok"}

    def database(self) -> DatabaseHealth:
        try:
            connected = self._health.ping()
        except Exception as exc:
            logger.warning("database health check failed: %s", exc)
            # The message is summarised rather than passed through: a driver
            # error can carry the host, port, and username from the DSN.
            return DatabaseHealth(connected=False, error=type(exc).__name__)

        present = self._health.present_schemas()
        missing = [s for s in EXPECTED_SCHEMAS if s not in present]
        return DatabaseHealth(
            connected=connected,
            server_version=self._health.server_version(),
            migration_revision=self._health.migration_revision(),
            schemas_present=present,
            schemas_missing=missing,
        )
