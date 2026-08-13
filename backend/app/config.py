"""Configuration, resolved from environment variables.

Nothing here has a hard-coded secret and nothing has a production default that
would be unsafe if the environment were missing.  ProductionConfig actively
refuses to start when a required value is absent, because a backend that boots
with dev authentication enabled in production is worse than one that does not
boot at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised at startup when the environment is not fit to run in."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _normalise_db_url(url: str) -> str:
    """Force the psycopg 3 driver, matching the migration layer."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


@dataclass
class Config:
    """Base configuration.  Subclasses tighten it."""

    ENV_NAME: str = "base"

    SQLALCHEMY_DATABASE_URI: str = ""
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: dict[str, object] = field(
        default_factory=lambda: {"pool_pre_ping": True, "future": True}
    )

    # Which authentication provider the app installs.  "dev" is a stand-in until
    # Firebase lands; see app/security/README in BACKEND_ARCHITECTURE.md §4.
    AUTH_PROVIDER: str = "dev"

    # Timezone used to derive core.report.occurred_hour / occurred_dow.  These are
    # plain columns, not generated ones: EXTRACT(... FROM timestamptz) is not
    # IMMUTABLE in PostgreSQL and cannot legally back a generated column, so the
    # application owns the conversion.
    CAMPUS_TIMEZONE: str = "Asia/Kolkata"

    # Server-side peppers.  Absent in dev, mandatory in production.
    AUDIT_IP_PEPPER: str | None = None
    REPORT_TOKEN_PEPPER: str | None = None

    # Firebase Authentication.  Only consulted when AUTH_PROVIDER=firebase.
    #
    # Nothing here is a default: a hard-coded project id would let a
    # misconfigured deployment silently verify tokens against the wrong Firebase
    # project, which is a working authentication system pointed at the wrong
    # population of users.
    FIREBASE_PROJECT_ID: str | None = None
    FIREBASE_CREDENTIALS_PATH: str | None = None
    FIREBASE_CREDENTIALS_JSON: str | None = None
    # Revocation checking costs a network round trip to Firebase on every single
    # request.  Off by default; worth turning on where an immediate sign-out must
    # take effect faster than the one-hour token lifetime.
    FIREBASE_CHECK_REVOKED: bool = False

    # Browser origins permitted to call the API. Explicit list, never "*":
    # the API is credentialed, and a wildcard origin on a credentialed API is
    # how any site a student visits gets to act as them.
    CORS_ORIGINS: list[str] = field(default_factory=lambda: ["http://localhost:5173"])

    # Object storage for evidence images.  "memory" is for tests and local work
    # without a bucket; ProductionConfig refuses it.
    STORAGE_PROVIDER: str = "memory"
    FIREBASE_STORAGE_BUCKET: str | None = None
    # 10 MB.  Generous for a phone photo, small enough that routing bytes through
    # Flask stays reasonable.  Defined once and served to the client by
    # GET /evidence/config so the two cannot disagree.
    MAX_IMAGE_UPLOAD_BYTES: int = 10 * 1024 * 1024
    # Hours before an upload that was never attached to a report is reaped.
    PENDING_UPLOAD_TTL_HOURS: int = 6

    # Path to the exported classification model, relative to the project root or
    # absolute. Absent or unreadable means no category suggestion — a supported
    # state, not a failure. Produce one with scripts/export_baseline_model.py.
    MODEL_ARTIFACT_PATH: str | None = None

    MAX_EVIDENCE_PER_REPORT: int = 5
    MAX_CONTENT_LENGTH: int = 1 * 1024 * 1024  # request body ceiling, not uploads

    JSON_SORT_KEYS: bool = False
    PROPAGATE_EXCEPTIONS: bool = False

    # Flask-Limiter's storage backend. "memory://" counts requests per WSGI
    # *process* — correct with a single worker, but each gunicorn worker
    # enforces its own independent limit rather than one shared one. Point
    # this at Redis (e.g. "redis://host:6379") for a true cross-process limit
    # in a multi-worker deployment; see PRODUCTION_READINESS.md.
    RATELIMIT_STORAGE_URI: str = "memory://"

    # Whether to trust X-Forwarded-For from the immediate upstream, via
    # werkzeug's ProxyFix. Off by default: trusting it without a proxy in
    # front is how a client spoofs the address the rate limiter keys on.
    # Turn on only when a single reverse proxy/load balancer sits in front
    # of this process and strips/sets that header itself.
    TRUST_PROXY_HEADERS: bool = False

    # On everywhere except TestingConfig. The whole suite runs in one process
    # sharing one in-memory limiter store across hundreds of tests; leaving
    # this on would make later tests fail from earlier tests' request counts,
    # not from anything they did themselves. Rate limiting's own tests build
    # their own app with this explicitly re-enabled.
    RATELIMIT_ENABLED: bool = True

    @classmethod
    def from_env(cls) -> Config:
        url = _env("DATABASE_URL")
        if not url:
            raise ConfigError(
                "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
                "or export it before starting the app."
            )
        cfg = cls()
        cfg.SQLALCHEMY_DATABASE_URI = _normalise_db_url(url)
        cfg.AUTH_PROVIDER = _env("AUTH_PROVIDER", cfg.AUTH_PROVIDER) or cfg.AUTH_PROVIDER
        cfg.CAMPUS_TIMEZONE = _env("CAMPUS_TIMEZONE", cfg.CAMPUS_TIMEZONE) or cfg.CAMPUS_TIMEZONE
        cfg.AUDIT_IP_PEPPER = _env("AUDIT_IP_PEPPER")
        cfg.REPORT_TOKEN_PEPPER = _env("REPORT_TOKEN_PEPPER")
        cfg.MAX_EVIDENCE_PER_REPORT = _env_int(
            "MAX_EVIDENCE_PER_REPORT", cfg.MAX_EVIDENCE_PER_REPORT
        )
        origins = _env("CORS_ORIGINS")
        if origins:
            cfg.CORS_ORIGINS = [o.strip() for o in origins.split(",") if o.strip()]
        cfg.STORAGE_PROVIDER = (
            _env("STORAGE_PROVIDER", cfg.STORAGE_PROVIDER) or cfg.STORAGE_PROVIDER
        )
        cfg.FIREBASE_STORAGE_BUCKET = _env("FIREBASE_STORAGE_BUCKET")
        cfg.MODEL_ARTIFACT_PATH = _env("MODEL_ARTIFACT_PATH")
        cfg.MAX_IMAGE_UPLOAD_BYTES = _env_int("MAX_IMAGE_UPLOAD_BYTES", cfg.MAX_IMAGE_UPLOAD_BYTES)
        cfg.PENDING_UPLOAD_TTL_HOURS = _env_int(
            "PENDING_UPLOAD_TTL_HOURS", cfg.PENDING_UPLOAD_TTL_HOURS
        )
        cfg.RATELIMIT_STORAGE_URI = (
            _env("RATELIMIT_STORAGE_URI", cfg.RATELIMIT_STORAGE_URI) or cfg.RATELIMIT_STORAGE_URI
        )
        cfg.TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", cfg.TRUST_PROXY_HEADERS)
        # Flask rejects a body over MAX_CONTENT_LENGTH before a route sees it.
        # Leave headroom for multipart framing, or a file exactly at the limit
        # fails with an unhelpful 413 instead of the sanitiser's message.
        cfg.MAX_CONTENT_LENGTH = cfg.MAX_IMAGE_UPLOAD_BYTES + (1024 * 1024)
        cfg.FIREBASE_PROJECT_ID = _env("FIREBASE_PROJECT_ID")
        cfg.FIREBASE_CREDENTIALS_PATH = _env("FIREBASE_CREDENTIALS_PATH")
        cfg.FIREBASE_CREDENTIALS_JSON = _env("FIREBASE_CREDENTIALS_JSON")
        cfg.FIREBASE_CHECK_REVOKED = _env_bool("FIREBASE_CHECK_REVOKED", False)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        self._validate_auth_provider()
        self._validate_storage_provider()

    def _validate_storage_provider(self) -> None:
        if self.STORAGE_PROVIDER not in {"memory", "firebase"}:
            raise ConfigError(
                f"unknown STORAGE_PROVIDER {self.STORAGE_PROVIDER!r}; "
                "expected 'memory' or 'firebase'"
            )
        if self.STORAGE_PROVIDER == "firebase" and not self.FIREBASE_STORAGE_BUCKET:
            raise ConfigError(
                "STORAGE_PROVIDER=firebase requires FIREBASE_STORAGE_BUCKET. Without it "
                "the application cannot know where evidence would be written, and would "
                "fail on the first upload rather than at startup."
            )
        if self.STORAGE_PROVIDER == "firebase" and not self.FIREBASE_PROJECT_ID:
            # Storage initialises its own Firebase Admin app (or reuses the one
            # auth created) keyed by project id, independent of AUTH_PROVIDER —
            # a deployment can use dev auth with real Firebase Storage.
            raise ConfigError(
                "STORAGE_PROVIDER=firebase requires FIREBASE_PROJECT_ID, even when "
                "AUTH_PROVIDER is not firebase. It identifies which Firebase project "
                "the storage bucket belongs to."
            )
        if self.MAX_IMAGE_UPLOAD_BYTES <= 0:
            raise ConfigError("MAX_IMAGE_UPLOAD_BYTES must be positive")

    def _validate_auth_provider(self) -> None:
        """Selecting Firebase without a project id is a startup error, not a
        runtime one.

        Left to fail later it surfaces as every login returning 401, which reads
        like a credential problem and sends whoever is debugging it to the wrong
        place entirely.
        """
        if self.AUTH_PROVIDER not in {"dev", "firebase"}:
            raise ConfigError(
                f"unknown AUTH_PROVIDER {self.AUTH_PROVIDER!r}; expected 'dev' or 'firebase'"
            )
        if self.AUTH_PROVIDER == "firebase" and not self.FIREBASE_PROJECT_ID:
            raise ConfigError(
                "AUTH_PROVIDER=firebase requires FIREBASE_PROJECT_ID. It is the "
                "audience an ID token is verified against; without it, tokens "
                "cannot be checked as belonging to this project."
            )

    def as_flask_mapping(self) -> dict[str, object]:
        return {k: v for k, v in vars(self).items() if k.isupper() or k == "ENV_NAME"}


@dataclass
class DevelopmentConfig(Config):
    ENV_NAME: str = "development"
    DEBUG: bool = True


@dataclass
class TestingConfig(Config):
    ENV_NAME: str = "testing"
    TESTING: bool = True
    DEBUG: bool = False
    PROPAGATE_EXCEPTIONS: bool = False
    RATELIMIT_ENABLED: bool = False

    @classmethod
    def from_env(cls) -> Config:
        url = _env("TEST_DATABASE_URL") or _env("DATABASE_URL")
        if not url:
            raise ConfigError("TEST_DATABASE_URL or DATABASE_URL must be set for tests.")
        cfg = cls()
        cfg.SQLALCHEMY_DATABASE_URI = _normalise_db_url(url)
        cfg.AUTH_PROVIDER = "dev"
        cfg.AUDIT_IP_PEPPER = "testing-pepper-not-secret"
        cfg.REPORT_TOKEN_PEPPER = "testing-pepper-not-secret"
        cfg.STORAGE_PROVIDER = "memory"
        return cfg


@dataclass
class ProductionConfig(Config):
    ENV_NAME: str = "production"
    DEBUG: bool = False

    def validate(self) -> None:
        if self.AUTH_PROVIDER == "dev":
            raise ConfigError(
                "AUTH_PROVIDER=dev is refused in production. The development "
                "provider trusts a request header and authenticates anyone who "
                "asks; it must never face the internet."
            )
        self._validate_auth_provider()
        self._validate_storage_provider()
        if self.STORAGE_PROVIDER == "memory":
            raise ConfigError(
                "STORAGE_PROVIDER=memory is refused in production. Evidence would be "
                "held in process memory and lost on restart."
            )
        if any(o == "*" or "localhost" in o for o in self.CORS_ORIGINS):
            raise ConfigError(
                "CORS_ORIGINS in production must be explicit https origins. A "
                "wildcard on a credentialed API lets any site act as a signed-in "
                "user; a localhost entry means the list was never reviewed."
            )
        missing = [
            name for name in ("AUDIT_IP_PEPPER", "REPORT_TOKEN_PEPPER") if not getattr(self, name)
        ]
        if missing:
            raise ConfigError(
                f"missing required secrets in production: {', '.join(missing)}. "
                'Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )


_CONFIGS: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def load_config(env_name: str | None = None) -> Config:
    name = (env_name or _env("FLASK_ENV") or "development").strip().lower()
    if name not in _CONFIGS:
        raise ConfigError(f"unknown environment {name!r}; expected one of {sorted(_CONFIGS)}")
    return _CONFIGS[name].from_env()
