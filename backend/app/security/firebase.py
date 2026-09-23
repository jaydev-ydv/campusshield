"""Firebase ID token verification.

**This is the only module in the codebase that imports ``firebase_admin``.**

Everything above it — the authentication provider, the authorisation policy,
services, repositories, routes — works against :class:`TokenVerifier`, a
two-method interface over "turn this string into a verified subject or raise".
That boundary is what lets the provider's own logic be tested exhaustively
without Firebase credentials, and it is why swapping identity providers again
later would touch this file and nothing else.

The import is lazy. ``firebase-admin`` pulls in Google's API client stack, and a
developer running the test suite with ``AUTH_PROVIDER=dev`` should not need it
installed.

What a verified token yields is deliberately narrow. :class:`VerifiedSubject`
exposes the UID and, for logging only, the email — **not** the claims dictionary.
A raw claims bag is an invitation to read ``claims["role"]``, and a Firebase
custom claim is attacker-influencable in exactly the scenarios where it matters
and stale for up to an hour after a legitimate role change. Roles come from
``identity.app_user``. Removing the dictionary removes the temptation.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..errors import AuthenticationError, ServiceUnavailableError

logger = logging.getLogger(__name__)


class FirebaseConfigurationError(RuntimeError):
    """Firebase is selected but not configured well enough to verify anything."""


@dataclass(frozen=True, slots=True)
class VerifiedSubject:
    """The result of a successful verification.

    Note the absences. No role, no claims dictionary, no raw token. The UID is
    a lookup key into ``identity.app_user`` and nothing more; ``email`` is
    carried for log lines and is not consulted by any authorisation decision.
    Institutional-domain checks are deliberately not implemented — the
    university's exact email domain has not been confirmed.
    """

    uid: str
    email: str | None = None
    email_verified: bool = False


@runtime_checkable
class TokenVerifier(Protocol):
    """Turn a credential into a verified subject, or raise."""

    def verify(self, token: str) -> VerifiedSubject:
        """Verify ``token``.

        Raises :class:`AuthenticationError` when the token is absent, malformed,
        expired, revoked, or otherwise invalid, and
        :class:`ServiceUnavailableError` when verification could not be attempted
        — those are different failures and must not be collapsed.
        """
        ...


class _PublicCertCredentialMarker:
    """Marker interface for public-key ID token verification credential."""


class FirebaseTokenVerifier:
    """Verifies Firebase ID tokens through the Firebase Admin SDK.

    Verification checks the RS256 signature against Google's rotating public
    keys, plus expiry, issued-at, audience (the project id) and issuer. All of
    that is the SDK's job; this class exists to initialise it from environment
    configuration and to translate its exception taxonomy into the application's.
    """

    def __init__(
        self,
        *,
        project_id: str,
        credentials_path: str | None = None,
        credentials_json: str | None = None,
        check_revoked: bool = False,
    ) -> None:
        if not project_id:
            raise FirebaseConfigurationError(
                "FIREBASE_PROJECT_ID is required when AUTH_PROVIDER=firebase."
            )
        self._project_id = project_id
        self._credentials_path = credentials_path
        self._credentials_json = credentials_json
        self._check_revoked = check_revoked
        self._app: Any = None

    # -- initialisation ----------------------------------------------------

    def _build_credential(self) -> Any:
        from firebase_admin import credentials

        if self._credentials_json:
            # For platforms that only offer environment variables. The value is
            # the service-account JSON itself, so it is a secret in the same
            # sense a private key is — never logged, never echoed in an error.
            try:
                payload = json.loads(self._credentials_json)
            except json.JSONDecodeError as exc:
                raise FirebaseConfigurationError(
                    "FIREBASE_CREDENTIALS_JSON is not valid JSON."
                ) from exc
            return credentials.Certificate(payload)

        if self._credentials_path:
            path = pathlib.Path(self._credentials_path).expanduser()
            if not path.is_file():
                raise FirebaseConfigurationError(
                    f"FIREBASE_CREDENTIALS_PATH points at {path}, which does not exist."
                )
            return credentials.Certificate(str(path))

        # Explicit environment variable pointing to a service-account JSON file
        google_app_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if google_app_creds:
            gpath = pathlib.Path(google_app_creds).expanduser()
            if gpath.is_file():
                return credentials.Certificate(str(gpath))
            try:
                return credentials.ApplicationDefault()
            except Exception as exc:
                raise FirebaseConfigurationError(
                    f"GOOGLE_APPLICATION_CREDENTIALS points at {gpath}, which could not be loaded."
                ) from exc

        # When check_revoked is True, privileged Admin API calls are required:
        if self._check_revoked:
            try:
                return credentials.ApplicationDefault()
            except Exception as exc:
                raise FirebaseConfigurationError(
                    "No Firebase credentials found. Set FIREBASE_CREDENTIALS_PATH to a "
                    "service-account JSON file, or FIREBASE_CREDENTIALS_JSON to its "
                    "contents, or GOOGLE_APPLICATION_CREDENTIALS for application "
                    "default credentials."
                ) from exc

        # Default development & standard verification mode:
        # ID token verification only needs FIREBASE_PROJECT_ID and Google's public certificates.
        class _PublicCertCredential(_PublicCertCredentialMarker, credentials.Base):
            def get_credential(self) -> Any:
                from google.auth.credentials import AnonymousCredentials

                return AnonymousCredentials()

        return _PublicCertCredential()

    def _check_credential_resolves(self, credential: Any) -> None:
        """Resolve the credential eagerly, but only when it is actually needed.

        Two facts shape this, and getting either wrong is expensive.

        **Verifying an ID token does not need the service-account key.** The SDK
        checks the signature against Google's public certificates; the project id
        is what makes the check specific to this project. Credentials are for
        privileged Admin operations — of which ``check_revoked=True`` is the only
        one this class performs, since it looks the user up through the Admin API.

        **Probing Application Default Credentials is not free.** Off Google
        infrastructure, resolution falls through to the metadata server at
        169.254.169.254 and blocks until that times out — measured at roughly
        nine seconds. Paying that on every boot to produce a warning about
        something verification does not need is a bad trade.

        So: an explicitly configured credential is validated here, because that
        is cheap and a wrong path is a typo worth catching. ADC is probed only
        when ``check_revoked`` makes it load-bearing, and is otherwise left to
        resolve if and when something needs it.
        """
        if isinstance(credential, _PublicCertCredentialMarker):
            logger.info(
                "Using public-key verification for Firebase ID tokens (project %s). "
                "No service-account credentials configured.",
                self._project_id,
            )
            return

        if not self._check_revoked and not (self._credentials_path or self._credentials_json):
            logger.info(
                "Firebase credentials not validated at startup: ID token verification "
                "needs only FIREBASE_PROJECT_ID and Google's public certificates. "
                "Set FIREBASE_CHECK_REVOKED=true or configure an explicit credential "
                "if a privileged Admin SDK call is required."
            )
            return

        try:
            credential.get_credential()
        except Exception as exc:
            detail = (
                "No usable Firebase credentials. Set FIREBASE_CREDENTIALS_PATH to a "
                "service-account JSON file, FIREBASE_CREDENTIALS_JSON to its contents, "
                "or GOOGLE_APPLICATION_CREDENTIALS for application default credentials."
            )
            if self._check_revoked:
                raise FirebaseConfigurationError(
                    f"{detail} They are required because FIREBASE_CHECK_REVOKED=true, "
                    "which looks each user up through the Firebase Admin API."
                ) from exc
            raise FirebaseConfigurationError(detail) from exc

    def initialize(self) -> None:
        """Initialise the SDK. Safe to call more than once.

        Called eagerly at application startup so a misconfiguration surfaces
        there, naming the variable to fix, rather than on the first student's
        first login.
        """
        if self._app is not None:
            return
        try:
            import firebase_admin
        except ImportError as exc:
            raise FirebaseConfigurationError(
                "firebase-admin is not installed. Run: pip install -r backend/requirements.txt"
            ) from exc

        name = f"campusshield-{self._project_id}"
        try:
            self._app = firebase_admin.get_app(name)
        except ValueError:
            credential = self._build_credential()
            self._check_credential_resolves(credential)
            self._app = firebase_admin.initialize_app(
                credential,
                options={"projectId": self._project_id},
                name=name,
            )
        # A named app, not the SDK default. Two Flask apps in one process (an
        # API and a worker, say) would otherwise fight over the global default.
        logger.info("Firebase Admin initialised for project %s", self._project_id)

    @property
    def app(self) -> Any:
        """The initialised Admin app, for callers that need to share it.

        Exists for exactly one caller: the storage provider, which must reuse
        this same app rather than resolving the SDK's global default (which
        this class deliberately never initialises — see ``initialize()``).
        ``firebase_admin.get_app(name)`` is idempotent, so a second verifier
        instance calling ``initialize()`` for the same project id resolves to
        this identical app rather than creating another one.
        """
        self.initialize()
        return self._app

    # -- verification ------------------------------------------------------

    def verify(self, token: str) -> VerifiedSubject:
        if not token or not token.strip():
            raise AuthenticationError("No ID token was presented.", code="TOKEN_MISSING")

        self.initialize()
        from firebase_admin import auth as firebase_auth

        try:
            claims = firebase_auth.verify_id_token(
                token, app=self._app, check_revoked=self._check_revoked
            )
        except firebase_auth.ExpiredIdTokenError as exc:
            # Distinct from a plain invalid token: the client should silently
            # refresh and retry, which it cannot know to do from a generic 401.
            raise AuthenticationError(
                "The ID token has expired. Refresh it and retry.",
                code="TOKEN_EXPIRED",
            ) from exc
        except firebase_auth.RevokedIdTokenError as exc:
            raise AuthenticationError(
                "The ID token has been revoked. Sign in again.",
                code="TOKEN_REVOKED",
            ) from exc
        except firebase_auth.UserDisabledError as exc:
            raise AuthenticationError(
                "This account has been disabled.", code="ACCOUNT_DISABLED"
            ) from exc
        except firebase_auth.CertificateFetchError as exc:
            # Not the caller's fault and not an authentication failure: we could
            # not reach Google to verify. A 401 here would tell a legitimate user
            # their credentials are bad when the truth is that we are degraded.
            logger.error("could not fetch Firebase signing certificates: %s", exc)
            raise ServiceUnavailableError(
                "Cannot verify credentials right now. Please retry shortly."
            ) from exc
        except (firebase_auth.InvalidIdTokenError, ValueError) as exc:
            # ValueError covers a non-string or structurally broken token, which
            # is what a raw UID or an API key pasted into the header looks like.
            logger.info("rejected an invalid Firebase ID token: %s", type(exc).__name__)
            raise AuthenticationError("The ID token is invalid.", code="TOKEN_INVALID") from exc
        except Exception as exc:
            from firebase_admin.exceptions import FirebaseError
            from google.auth.exceptions import DefaultCredentialsError, GoogleAuthError

            if isinstance(exc, DefaultCredentialsError):
                logger.error("Firebase default credentials error: %s", exc)
                raise ServiceUnavailableError(
                    "Firebase server credentials are not configured or invalid."
                ) from exc
            if isinstance(exc, GoogleAuthError):
                logger.error("Google authentication error during token verification: %s", exc)
                raise ServiceUnavailableError(
                    "Cannot verify credentials right now. Please check server connectivity."
                ) from exc
            if isinstance(exc, FirebaseError):
                logger.error("Firebase error during token verification: %s", exc)
                raise ServiceUnavailableError(
                    "Firebase authentication service error. Please retry shortly."
                ) from exc
            raise

        uid = claims.get("uid") or claims.get("sub")
        if not uid:
            # Should be unreachable — the SDK guarantees a subject on success —
            # but a verified token with no subject must never resolve to a user.
            raise AuthenticationError("The ID token carries no subject.", code="TOKEN_INVALID")

        return VerifiedSubject(
            uid=str(uid),
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified", False)),
        )


def build_verifier(config: Any) -> FirebaseTokenVerifier:
    """Construct a verifier from application config."""
    return FirebaseTokenVerifier(
        project_id=config.get("FIREBASE_PROJECT_ID") or "",
        credentials_path=config.get("FIREBASE_CREDENTIALS_PATH"),
        credentials_json=config.get("FIREBASE_CREDENTIALS_JSON"),
        check_revoked=bool(config.get("FIREBASE_CHECK_REVOKED", False)),
    )
