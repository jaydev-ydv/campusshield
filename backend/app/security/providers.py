"""Authentication providers.

A provider owns two things: how a credential arrives on the wire, and how it
resolves to a :class:`~app.security.principal.Principal`.

    extract_credential(headers) -> str | None
    authenticate(credential)    -> Principal | None

``None`` from either means "no credential was presented" — an anonymous caller
on a public endpoint. Raising ``AuthenticationError`` means "a credential was
presented and it is bad". The distinction matters: the first is normal traffic,
the second is worth logging.

Providers receive a header **mapping**, not a Flask request. No Flask import
here, so providers can be unit-tested with no application context. Each owns its
own wire format because the formats genuinely differ — ``X-Dev-User`` versus
``Authorization: Bearer`` — and putting that decision anywhere else means two
places to change when a provider is swapped.

Two implementations:

* :class:`DevAuthProvider` — local development and tests only. Trusts a header.
* :class:`FirebaseAuthProvider` — production. Delegates cryptographic
  verification to a :class:`~app.security.firebase.TokenVerifier` and never
  imports ``firebase_admin`` itself.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AuthenticationError
from ..models import AppUser
from .firebase import TokenVerifier, VerifiedSubject
from .principal import Principal

logger = logging.getLogger(__name__)

AUTHORIZATION_HEADER = "Authorization"
DEV_USER_HEADER = "X-Dev-User"
BEARER_PREFIX = "bearer "


@runtime_checkable
class AuthProvider(Protocol):
    """What every authentication mechanism must provide."""

    name: str

    def extract_credential(self, headers: Mapping[str, str]) -> str | None:
        """Pull this provider's credential out of the request headers."""
        ...

    def authenticate(self, credential: str | None) -> Principal | None:
        """Resolve a credential to a Principal, or None if there is none."""
        ...

    def identify(self, credential: str | None) -> VerifiedSubject | None:
        """Verify a credential and return the external subject, WITHOUT
        requiring a local account.

        This exists for exactly one caller: account provisioning. A user who has
        just signed up with Firebase has a valid token and no
        ``identity.app_user`` row, so ``authenticate`` correctly rejects them —
        which would make registration impossible without a way to say "this
        credential is genuine, but there is nobody here yet".

        It is deliberately narrower than ``authenticate``: it returns a verified
        external identity, never a Principal, so it cannot be mistaken for
        authorisation. Nothing it returns carries a role.
        """
        ...


class _UserLookupMixin:
    """Resolution from an external subject to a local Principal.

    Both providers end here, and this is the single most important decision in
    the authentication design: **role comes from ``identity.app_user``, never
    from the credential.**

    A Firebase custom claim would be the obvious alternative and is wrong twice
    over. It can be stale for up to an hour after a role change, because a token
    already issued keeps whatever it was minted with. And it makes a leaked token
    carry its privileges with it, rather than being a key that has to be looked
    up against a table someone can revoke. The identity provider says who you
    are; the database says what you may do.
    """

    def _principal_for(self, session: Session, *, firebase_uid: str) -> Principal:
        user = session.scalar(select(AppUser).where(AppUser.firebase_uid == firebase_uid))
        if user is None:
            # The token verified, so the caller is a genuine Firebase user — they
            # simply have no account here. Deliberately not distinguished from a
            # bad token in the response: an attacker holding any valid token from
            # the project could otherwise enumerate which UIDs have accounts.
            logger.info("no application account for a verified subject")
            raise AuthenticationError("No account exists for these credentials.")
        if not user.is_active:
            raise AuthenticationError("This account is deactivated.")
        return Principal(
            user_id=user.user_id,
            role=user.role,
            email=user.institutional_email,
            is_active=user.is_active,
            display_name=user.display_name,
        )


def _bearer_token(headers: Mapping[str, str]) -> str | None:
    """Extract a bearer token, tolerating case but nothing else.

    Returns ``None`` for a missing header. Raises for a header that is present
    but not a bearer credential — a client sending ``Authorization: <raw uid>``
    or ``Basic ...`` has made a mistake it needs told about, and silently
    treating it as anonymous turns a 401 into a confusing 403 further along.
    """
    raw = headers.get(AUTHORIZATION_HEADER)
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if not value.lower().startswith(BEARER_PREFIX):
        raise AuthenticationError(
            "Authorization header must use the Bearer scheme.",
            code="MALFORMED_AUTHORIZATION",
            details={"expected": "Authorization: Bearer <Firebase ID token>"},
        )
    token = value[len(BEARER_PREFIX) :].strip()
    if not token:
        raise AuthenticationError(
            "Authorization header carries no token.",
            code="MALFORMED_AUTHORIZATION",
        )
    if " " in token:
        # "Bearer a b" — almost always a copy-paste that swallowed a newline.
        raise AuthenticationError("Malformed bearer token.", code="MALFORMED_AUTHORIZATION")
    return token


class DevAuthProvider(_UserLookupMixin):
    """Development-only authentication. **Never enable this in production.**

    The credential is an ``identity.app_user`` UUID, a ``firebase_uid``, or an
    institutional email, in the ``X-Dev-User`` header. There is no verification
    of any kind: whoever sends the header becomes that user.

    That is the point — it lets the API be exercised without minting real ID
    tokens, and it keeps the test suite free of network calls and service-account
    files. It is contained by three things: it resolves against the real user
    table rather than fabricating principals, so roles and authorisation behave
    exactly as they will in production; ``ProductionConfig.validate()`` refuses
    to start when it is selected; and ``build_provider`` logs a warning on every
    boot.
    """

    name = "dev"

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def extract_credential(self, headers: Mapping[str, str]) -> str | None:
        return headers.get(DEV_USER_HEADER) or None

    def identify(self, credential: str | None) -> VerifiedSubject | None:
        # No verification, as everywhere else in this provider. The header value
        # is taken as the subject id.
        if not credential:
            return None
        return VerifiedSubject(uid=credential.strip())

    def authenticate(self, credential: str | None) -> Principal | None:
        if not credential:
            return None
        session: Session = self._session_factory()
        identifier = credential.strip()

        user: AppUser | None = None
        try:
            user_uuid = uuid.UUID(identifier)
        except ValueError:
            user_uuid = None

        if user_uuid is not None:
            user = session.get(AppUser, user_uuid)
        if user is None:
            user = session.scalar(select(AppUser).where(AppUser.firebase_uid == identifier))
        if user is None:
            user = session.scalar(
                select(AppUser).where(AppUser.institutional_email.ilike(identifier))
            )
        if user is None:
            raise AuthenticationError("Unknown development user.")
        if not user.is_active:
            raise AuthenticationError("This account is deactivated.")

        return Principal(
            user_id=user.user_id,
            role=user.role,
            email=user.institutional_email,
            is_active=user.is_active,
            display_name=user.display_name,
        )


class FirebaseAuthProvider(_UserLookupMixin):
    """Firebase Authentication.

        React → Firebase Auth → ID token → Authorization: Bearer <token>
              → this provider → TokenVerifier → identity.app_user → Principal

    The cryptography is not here. It lives behind :class:`TokenVerifier`, so this
    class holds only the parts worth reading: get the bearer token, verify it,
    look the subject up locally, and take the role from the row rather than the
    token.

    Two rules the structure enforces rather than documents:

    * A client-supplied UID is never proof of identity. The UID used for lookup
      comes from ``VerifiedSubject``, which only a successful signature check can
      produce. ``Authorization: Bearer <uid>`` fails verification like any other
      non-token string.
    * A client-supplied role is never consulted. There is no claims dictionary to
      read one from — :class:`~app.security.firebase.VerifiedSubject` does not
      carry one.
    """

    name = "firebase"

    def __init__(self, session_factory, verifier: TokenVerifier) -> None:
        self._session_factory = session_factory
        self._verifier = verifier

    def extract_credential(self, headers: Mapping[str, str]) -> str | None:
        return _bearer_token(headers)

    def identify(self, credential: str | None) -> VerifiedSubject | None:
        if not credential:
            return None
        return self._verifier.verify(credential)

    def authenticate(self, credential: str | None) -> Principal | None:
        if not credential:
            return None
        subject = self._verifier.verify(credential)
        session: Session = self._session_factory()
        return self._principal_for(session, firebase_uid=subject.uid)


def build_provider(name: str, session_factory, *, config=None) -> AuthProvider:
    """Construct the configured provider, failing at startup if it cannot work."""
    if name == "dev":
        logger.warning(
            "DEVELOPMENT AUTHENTICATION ENABLED — any caller sending an %s header "
            "is trusted without verification. This must never face the internet.",
            DEV_USER_HEADER,
        )
        return DevAuthProvider(session_factory)

    if name == "firebase":
        from .firebase import build_verifier

        verifier = build_verifier(config or {})
        # Eagerly initialise so a missing project id or unreadable service-account
        # file fails here, naming the variable to fix, rather than on the first
        # student's first login.
        verifier.initialize()
        return FirebaseAuthProvider(session_factory, verifier)

    raise RuntimeError(f"unknown AUTH_PROVIDER {name!r}; expected 'dev' or 'firebase'")
