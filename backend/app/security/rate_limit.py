"""Request-rate abuse controls (Phase 4G).

The smallest mechanism that fits this architecture: `flask_extensions.
limiter` (Flask-Limiter), keyed by remote address, with a generous global
default applied everywhere and a stricter limit on the two routes with the
highest abuse/cost potential — account provisioning and evidence upload
(`@limiter.limit(...)` on the view functions themselves, in `routes/auth.py`
and `routes/evidence.py`).

This sits **alongside, not instead of**, the existing daily report-submission
quota (`report_service.py`'s `_enforce_quota`), which caps content volume per
account rather than request rate per address — a different guarantee, kept
deliberately separate rather than merged into one mechanism.

Two things this does not, and cannot, fix by itself:

**The storage backend matters.** `RATELIMIT_STORAGE_URI` defaults to
`memory://`, which counts requests per WSGI *process*. Behind gunicorn with
more than one worker, each worker enforces its own independent limit rather
than one shared one — a real limit, just not a single global one. Point it
at Redis for a true cross-process limit; see `PRODUCTION_READINESS.md`.

**The address must be genuine.** `get_remote_address` reads
`request.remote_addr`, the direct TCP peer. Behind a reverse proxy or load
balancer, that is the proxy's own address unless something restores the
real one from `X-Forwarded-For` — which is only safe to trust when exactly
one, known, trusted proxy sits in front and sets that header itself; trusting
it otherwise is how a client spoofs the address every limit is keyed on. See
`TRUST_PROXY_HEADERS`.
"""

from __future__ import annotations

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from ..extensions import limiter


def register_rate_limiting(app: Flask) -> None:
    if app.config.get("TRUST_PROXY_HEADERS"):
        # x_for=1: trust exactly one hop of X-Forwarded-For, i.e. exactly one
        # reverse proxy immediately in front of this process. A deployment
        # with more than one proxy in the chain needs a larger value.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    limiter.init_app(app)

    if (
        app.config.get("ENV_NAME") == "production"
        and app.config.get("RATELIMIT_STORAGE_URI", "memory://") == "memory://"
    ):
        app.logger.warning(
            "RATE LIMITING IS PROCESS-LOCAL — RATELIMIT_STORAGE_URI is unset (default "
            "memory://). With more than one gunicorn worker, each worker enforces its "
            "own independent limit rather than one shared one. Set RATELIMIT_STORAGE_URI "
            "to a Redis URL for a true cross-process limit, or run a single worker."
        )
