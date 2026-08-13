"""Application factory.

Assembly only: configuration, extensions, the authentication provider, error
handlers, and blueprints. No business logic and no route definitions live here.

``db.create_all()`` is never called. The schema belongs to Alembic, which owns
triggers, partial indexes, and CHECK constraints that SQLAlchemy cannot express;
letting the ORM create tables would produce a database that looks right and
enforces none of the guarantees.
"""

from __future__ import annotations

import logging
import sys

from flask import Flask, jsonify
from flask_cors import CORS

from .config import Config, load_config
from .errors import register_error_handlers
from .extensions import db
from .utils.correlation import RequestIdLogFilter, register_correlation

API_PREFIX = "/api/v1"


def _configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] [%(request_id)s] %(message)s")
    )
    handler.addFilter(RequestIdLogFilter())
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if app.config.get("DEBUG") else logging.INFO)
    # Werkzeug's per-request access log is noise next to structured app logs.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _build_storage_provider(app: Flask):
    """Construct the configured object-storage provider."""
    provider_name = app.config["STORAGE_PROVIDER"]

    if provider_name == "memory":
        from .storage.memory_provider import InMemoryStorageProvider

        app.logger.warning(
            "IN-MEMORY EVIDENCE STORAGE — uploaded images are held in process "
            "memory and lost on restart. Local development and tests only."
        )
        return InMemoryStorageProvider()

    if provider_name == "firebase":
        from .security.firebase import build_verifier
        from .storage.firebase_provider import FirebaseStorageProvider

        # Reuse the exact credential resolution and named-app initialisation
        # that authentication uses. firebase_admin.get_app(name) is idempotent
        # by project id, so when AUTH_PROVIDER=firebase too, this resolves to
        # the identical app the auth provider already created — one credential
        # configuration, not two, as app.storage.firebase_provider's own
        # docstring promises. When AUTH_PROVIDER=dev (storage-only Firebase),
        # this creates that one app itself.
        verifier = build_verifier(app.config)
        return FirebaseStorageProvider(
            bucket_name=app.config["FIREBASE_STORAGE_BUCKET"], app=verifier.app
        )

    raise RuntimeError(f"unknown STORAGE_PROVIDER {provider_name!r}")


def _load_model_bundle(app: Flask):
    """Read the serving model once, at startup.

    Returns None rather than raising when there is no artifact. A deployment
    without a trained model is a supported state: reporting, evidence and the
    responder map all work, and reports simply arrive without a category
    suggestion. Failing to boot over an optional assist would be the wrong
    trade.

    A *broken* artifact is logged loudly, because that is a mistake rather than
    a choice.
    """
    path = app.config.get("MODEL_ARTIFACT_PATH")
    if not path:
        return None

    from .ml.artifact import ArtifactError, load_bundle

    try:
        return load_bundle(path)
    except ArtifactError as exc:
        app.logger.warning(
            "no classification model loaded (%s). Reports will be accepted and "
            "risk-scored, but will carry no category suggestion.",
            exc,
        )
        return None


def create_app(config: Config | None = None, *, env_name: str | None = None) -> Flask:
    app = Flask(__name__)

    cfg = config or load_config(env_name)
    app.config.update(cfg.as_flask_mapping())

    _configure_logging(app)
    db.init_app(app)

    # The browser blocks a cross-origin call before the server ever sees it, so
    # the React dev server on :5173 cannot reach :5000 without this. Scoped to
    # the API prefix and to an explicit origin list — never "*", because the API
    # is credentialed. Authorization is listed explicitly: it is not a
    # CORS-safelisted request header, so a preflight fails without it.
    CORS(
        app,
        resources={f"{API_PREFIX}/*": {"origins": app.config["CORS_ORIGINS"]}},
        allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Report-Token"],
        expose_headers=["X-Request-ID"],
        methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        max_age=3600,
    )

    # Import models for their side effect of registering with the declarative
    # registry. They map onto tables Alembic already created.
    from . import models  # noqa: F401
    from .security.providers import build_provider

    # Built once at startup, not per request. For Firebase this also initialises
    # the Admin SDK, so a missing project id or unreadable service-account file
    # fails here — naming the variable to fix — rather than on a student's first
    # login.
    app.extensions["auth_provider"] = build_provider(
        app.config["AUTH_PROVIDER"], lambda: db.session, config=app.config
    )

    from .security.context import register_authentication
    from .security.rate_limit import register_rate_limiting
    from .security.response_headers import register_security_headers

    # The storage provider, installed once. Nothing above app/storage/ imports
    # Firebase Storage, so this is the only line that decides where evidence
    # bytes go — and the only line a test needs to change to run without a bucket.
    app.extensions["storage_provider"] = _build_storage_provider(app)

    # Loaded once per process, not per request. A few hundred kilobytes.
    app.extensions["model_bundle"] = _load_model_bundle(app)

    register_correlation(app)
    register_authentication(app)
    register_error_handlers(app)
    register_security_headers(app)
    register_rate_limiting(app)

    from .routes.auth import bp as auth_bp
    from .routes.catalog import bp as catalog_bp
    from .routes.evidence import bp as evidence_bp
    from .routes.health import bp as health_bp
    from .routes.incidents import bp as incidents_bp
    from .routes.notifications import bp as notifications_bp
    from .routes.reports import bp as reports_bp

    app.register_blueprint(health_bp, url_prefix=API_PREFIX)
    app.register_blueprint(auth_bp, url_prefix=API_PREFIX)
    app.register_blueprint(catalog_bp, url_prefix=API_PREFIX)
    app.register_blueprint(evidence_bp, url_prefix=API_PREFIX)
    app.register_blueprint(incidents_bp, url_prefix=API_PREFIX)
    app.register_blueprint(notifications_bp, url_prefix=API_PREFIX)
    app.register_blueprint(reports_bp, url_prefix=API_PREFIX)

    @app.get("/")
    def index():
        return (
            jsonify(
                {
                    "service": "CampusShield API",
                    "phase": 1,
                    "api_base": API_PREFIX,
                    "auth_provider": app.config["AUTH_PROVIDER"],
                    "endpoints": [
                        f"GET  {API_PREFIX}/health",
                        f"GET  {API_PREFIX}/health/db",
                        f"POST {API_PREFIX}/auth/register",
                        f"POST {API_PREFIX}/evidence",
                        f"DEL  {API_PREFIX}/evidence/<upload_token>",
                        f"GET  {API_PREFIX}/evidence/<evidence_id>",
                        f"GET  {API_PREFIX}/auth/me",
                        f"PATCH {API_PREFIX}/auth/me",
                        f"GET  {API_PREFIX}/locations",
                        f"GET  {API_PREFIX}/categories",
                        f"POST {API_PREFIX}/reports",
                        f"GET  {API_PREFIX}/reports/mine",
                        f"GET  {API_PREFIX}/reports/<public_ref>",
                        f"GET  {API_PREFIX}/incidents",
                        f"GET  {API_PREFIX}/incidents/<public_ref>",
                        f"POST {API_PREFIX}/incidents/<public_ref>/dispatch",
                        f"POST {API_PREFIX}/incidents/<public_ref>/dispatch/state",
                        f"POST {API_PREFIX}/incidents/<public_ref>/category",
                        f"POST {API_PREFIX}/incidents/<public_ref>/links/<link_id>",
                        f"POST {API_PREFIX}/incidents/<public_ref>/status",
                        f"POST {API_PREFIX}/incidents/<public_ref>/assign",
                        f"POST {API_PREFIX}/incidents/<public_ref>/unassign",
                        f"GET  {API_PREFIX}/notifications",
                        f"POST {API_PREFIX}/notifications/<notification_id>/read",
                    ],
                }
            ),
            200,
        )

    @app.teardown_appcontext
    def _remove_session(exception=None):
        # A request that raised must not leave a dirty session for the next one
        # to inherit and commit.
        if exception is not None:
            db.session.rollback()
        db.session.remove()

    app.logger.info(
        "CampusShield API ready (env=%s, auth=%s)",
        app.config.get("ENV_NAME"),
        app.config["AUTH_PROVIDER"],
    )
    return app
