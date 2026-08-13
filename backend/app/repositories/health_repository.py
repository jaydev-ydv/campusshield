"""Database connectivity and schema-presence checks."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

EXPECTED_SCHEMAS = (
    "identity",
    "core",
    "evidence",
    "ml",
    "analytics",
    "intervention",
    "notify",
    "audit",
)


class HealthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ping(self) -> bool:
        return self._session.scalar(text("SELECT 1")) == 1

    def server_version(self) -> str:
        return str(self._session.scalar(text("SHOW server_version")))

    def present_schemas(self) -> list[str]:
        rows = self._session.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = ANY(:names) ORDER BY schema_name"
            ),
            {"names": list(EXPECTED_SCHEMAS)},
        )
        return [row[0] for row in rows]

    def migration_revision(self) -> str | None:
        """The Alembic revision the database is on.

        A live connection is not the same as a usable database.  If the schema is
        a migration behind, connectivity looks perfect right up until the first
        insert fails, so the health check reports the revision rather than only a
        successful ping.
        """
        try:
            return self._session.scalar(text("SELECT version_num FROM public.alembic_version"))
        except Exception:
            return None
