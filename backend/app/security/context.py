"""Request-scoped authentication: header parsing, the ``@authenticated``
decorator, and access to the current principal.

This is the only module in the security layer that imports Flask.  Providers,
principals, and the authorisation policy stay framework-free so they can be
tested directly and so replacing the provider cannot change request parsing.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TypeVar

from flask import current_app, g, request

from ..errors import AuthenticationError, AuthorizationError
from ..models.enums import UserRole
from .principal import Principal

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., object])

AUTHORIZATION_HEADER = "Authorization"
DEV_USER_HEADER = "X-Dev-User"
REPORT_TOKEN_HEADER = "X-Report-Token"


def _current_provider():
    return current_app.extensions["auth_provider"]


def register_authentication(app) -> None:
    """Clear any cached principal at the start of every request.

    ``g`` lives on the *application* context, not the request context, and Flask
    reuses an existing application context rather than pushing a new one when it
    finds one already active — which happens in tests, and in any embedding that
    pushes its own context around a series of requests.

    Without this, a principal cached by one request survives into the next and
    the second caller is authenticated as the first. That is not a test artefact;
    it is a cross-user authentication bug that happens to be easiest to observe
    in a test. Resolving the credential once per request removes the possibility
    entirely.
    """

    @app.before_request
    def _reset_principal() -> None:
        g.pop("principal", None)


def load_principal() -> Principal | None:
    """Resolve and cache the principal for this request.

    The provider owns both halves — which header carries its credential and what
    that credential means — so this function stays the same whether the caller
    presented a development header or a Firebase ID token.
    """
    if "principal" in g:
        return g.principal
    provider = _current_provider()
    credential = provider.extract_credential(request.headers)
    principal = provider.authenticate(credential)
    g.principal = principal
    return principal


def current_principal() -> Principal | None:
    return load_principal()


def require_principal() -> Principal:
    principal = load_principal()
    if principal is None:
        raise AuthenticationError(
            "Authentication is required for this endpoint.",
            details={"hint": _auth_hint()},
        )
    return principal


def _auth_hint() -> str:
    if current_app.config.get("AUTH_PROVIDER") == "dev":
        return f"Development mode: send {DEV_USER_HEADER}: <user-id|firebase-uid|email>"
    return f"Send {AUTHORIZATION_HEADER}: Bearer <Firebase ID token>"


def report_token() -> str | None:
    """The one-time token an anonymous reporter received at submission."""
    return request.headers.get(REPORT_TOKEN_HEADER) or None


def authenticated(func: F) -> F:
    """Require an authenticated caller."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        require_principal()
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def require_roles(*roles: UserRole) -> Callable[[F], F]:
    """Require one of the given roles.

    Coarse gate only — it decides who may reach an endpoint, never which rows
    they may see.  Row-level access is :mod:`app.security.authorization`, and no
    endpoint may substitute this decorator for that policy.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            principal = require_principal()
            if principal.role not in roles:
                logger.info(
                    "role check refused user %s (%s) at %s",
                    principal.user_id,
                    principal.role.value,
                    request.path,
                )
                raise AuthorizationError("Your role does not have access to this endpoint.")
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def optional_authentication(func: F) -> F:
    """Attach a principal if one is presented, but do not require it.

    For endpoints reachable both by a signed-in student and by an anonymous
    reporter holding a report token.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            load_principal()
        except AuthenticationError:
            # A bad credential on an optional-auth endpoint is still a failure:
            # silently downgrading to anonymous would make a typo look like a
            # permissions problem later.
            raise
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
