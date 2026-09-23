"""Flask extensions, instantiated unbound and initialised by the app factory."""

from __future__ import annotations

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Kept independent of Flask-SQLAlchemy's own base so that models and
    repositories can be imported and unit-tested without an application context.
    """


db = SQLAlchemy(model_class=Base)
migrate = Migrate()

# Keyed by remote address. Generous enough not to obstruct a responder
# triaging a busy queue, tight enough to stop a basic scripted crawl of the
# API; individual routes add a stricter limit with @limiter.limit(...) where
# the abuse/cost potential is higher than average (see routes/auth.py,
# routes/evidence.py). Passed to the constructor rather than left to
# app.config: Flask-Limiter parses default_limits once, at construction —
# setting it as a plain attribute afterwards is silently a no-op. See
# app/security/rate_limit.py for the storage-backend and proxy-address
# caveats that make this correct in production.
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per hour", "30 per minute"])
