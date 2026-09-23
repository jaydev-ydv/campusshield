"""Alembic environment for CampusShield.

Resolves the database URL in this order:

1. ``-x db_url=...`` passed on the Alembic command line
2. A live Flask application context, if one exists (``SQLALCHEMY_DATABASE_URI``)
3. The ``DATABASE_URL`` environment variable, loaded from ``.env`` if present

Step 2 is what makes this directory drop-in compatible with Flask-Migrate.
Flask-Migrate is Alembic plus a Flask CLI wrapper and this same ``migrations/``
layout; when the backend lands, ``Migrate(app, db)`` will find this directory and
``flask db upgrade`` will work with no changes to the migration files.  Until
then the layer runs standalone, which keeps the schema independent of any
application code.

There is deliberately no ``target_metadata`` and autogenerate is not used.  The
schema is hand-written DDL — cross-schema foreign keys, partial unique indexes,
PL/pgSQL triggers, row-level security, and CHECK constraints that SQLAlchemy
cannot model.  Autogenerate would silently propose dropping every one of them.
"""

from __future__ import annotations

import os
import pathlib
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

# Logging is configured from alembic.ini when run via the CLI.
if config.config_file_name is not None:
    from logging.config import fileConfig

    ini_path = config.config_file_name
    if not os.path.exists(ini_path):
        root_ini = pathlib.Path(__file__).resolve().parent.parent / "alembic.ini"
        ini_path = str(root_ini) if root_ini.exists() else None
    if ini_path and os.path.exists(ini_path):
        fileConfig(ini_path)


def _load_dotenv() -> None:
    """Load .env from the project root or backend/.env if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        return
    root_dir = pathlib.Path(__file__).resolve().parent.parent
    env_path = root_dir / ".env"
    backend_env_path = root_dir / "backend" / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    elif backend_env_path.exists():
        load_dotenv(backend_env_path)


def _normalise(url: str) -> str:
    """Ensure a supported PostgreSQL driver is specified.

    Supports both psycopg 3 (psycopg) and psycopg 2 (psycopg2-binary).
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    if url.startswith("postgresql+psycopg://"):
        try:
            import psycopg  # noqa: F401
        except ImportError:
            try:
                import psycopg2  # noqa: F401
                return url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
            except ImportError:
                pass
        return url

    if url.startswith("postgresql+psycopg2://"):
        return url

    if url.startswith("postgresql://"):
        try:
            import psycopg  # noqa: F401
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        except ImportError:
            try:
                import psycopg2  # noqa: F401
                return url.replace("postgresql://", "postgresql+psycopg2://", 1)
            except ImportError:
                return url

    return url


def get_url() -> str:
    # 1. explicit override:  alembic -x db_url=postgresql+psycopg://...
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return _normalise(x_args["db_url"])

    # 2. a live Flask app (present once the backend exists)
    try:
        from flask import current_app

        return _normalise(current_app.config["SQLALCHEMY_DATABASE_URI"])
    except Exception:
        pass

    # 3. environment
    _load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.stderr.write(
            "\nERROR: no database URL.\n\n"
            "Set DATABASE_URL in .env (copy .env.example), or pass it directly:\n"
            "  alembic -x db_url=postgresql+psycopg://localhost:5432/campusshield upgrade head\n\n"
        )
        raise SystemExit(2)
    return _normalise(url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url=get_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            compare_type=False,
            # The whole migration runs in one transaction.  PostgreSQL supports
            # transactional DDL, so a failure half-way through leaves an empty
            # database rather than a partially-built one.
            transaction_per_migration=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
