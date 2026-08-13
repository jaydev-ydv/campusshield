"""Baseline security headers, applied to every response.

A credentialed, browser-facing JSON API needs the same defensive headers a
traditional web app does, even though it renders no HTML of its own: an
error page framed inside another site, a response sniffed as something it
is not, or a ``Referer`` header carrying a report reference to a third
party are all still real risks for an API a browser talks to directly.

``setdefault`` throughout: a route that has already set its own, more
specific header — the evidence endpoint's own ``Content-Security-Policy:
default-src 'none'; sandbox``, built for streaming image bytes rather than
JSON — is never overridden by the global default.
"""

from __future__ import annotations

from flask import Flask, Response


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def _apply(response: Response) -> Response:
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Content-Security-Policy", "default-src 'none'")
        headers.setdefault("Referrer-Policy", "no-referrer")
        # Inert over plain HTTP — browsers only honour this over HTTPS, so it
        # is safe to send in every environment, local development included.
        headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response
