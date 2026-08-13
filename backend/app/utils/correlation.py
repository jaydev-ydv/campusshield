"""Per-request correlation identifier.

Every request gets one, it appears in every log line, every error body, and the
``X-Request-ID`` response header.  A client-supplied header is honoured so a
trace can span the frontend and backend, but only if it looks like an id — an
unvalidated echo of client input into log files is how log injection works.
"""

from __future__ import annotations

import logging
import re
import uuid

from flask import Flask, g, has_request_context, request

_HEADER = "X-Request-ID"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestIdLogFilter(logging.Filter):
    """Attaches the current request's correlation id to every log record.

    Without this, "quote a request_id, find it in the logs" — the promise
    ``app.errors`` makes to an API caller — is not actually possible: the id
    was in ``g`` and in the response header, but never in a log line. Outside
    a request (startup, a CLI script) there is no ``g`` to read, so the field
    falls back to a placeholder rather than raising.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = (  # type: ignore[attr-defined]
            g.request_id if has_request_context() and "request_id" in g else "-"
        )
        return True


def new_request_id() -> str:
    return str(uuid.uuid4())


def _incoming_request_id() -> str | None:
    candidate = request.headers.get(_HEADER)
    if candidate and _SAFE.match(candidate):
        return candidate
    return None


def register_correlation(app: Flask) -> None:
    @app.before_request
    def _assign() -> None:
        g.request_id = _incoming_request_id() or new_request_id()

    @app.after_request
    def _echo(response):
        response.headers[_HEADER] = g.get("request_id", "unknown")
        return response


def current_request_id() -> str:
    return g.get("request_id", "unknown")
