"""Firebase Authentication.

**No real Firebase credentials are used here, and no successful verification is
faked.** The split is deliberate:

* Signature checking, expiry, audience and issuer are the Firebase Admin SDK's
  job. Reimplementing or stubbing that would test a fiction.
* Everything the application actually owns — bearer extraction, error mapping,
  UID-to-user resolution, where the role comes from, what happens to an unknown
  subject — is exercised in full against a stub :class:`TokenVerifier`.

The stub replaces the *cryptography*, not the provider. Every line of
``FirebaseAuthProvider`` runs. What remains untested without credentials is
whether Google's certificates verify a real token, and that is stated plainly in
BACKEND_ARCHITECTURE.md §5 along with what to configure to close the gap.
"""

from __future__ import annotations

import uuid

import pytest

from app.errors import AuthenticationError, ServiceUnavailableError
from app.models.enums import UserRole
from app.security.firebase import (
    FirebaseConfigurationError,
    FirebaseTokenVerifier,
    TokenVerifier,
    VerifiedSubject,
)
from app.security.providers import (
    AuthProvider,
    DevAuthProvider,
    FirebaseAuthProvider,
    build_provider,
)

VALID_TOKEN = "a.valid.looking.token"
EXPIRED_TOKEN = "an.expired.token"
INVALID_TOKEN = "not.a.real.token"
REVOKED_TOKEN = "a.revoked.token"
UNREACHABLE_TOKEN = "certs.unreachable.token"


class StubVerifier:
    """Stands in for Google's signature check, and nothing else.

    Maps a handful of fixed strings to the outcomes the real SDK produces, so the
    provider's handling of each can be exercised. It never returns a subject for
    an unrecognised string — a stub that succeeded by default would quietly turn
    "reject invalid tokens" into an untested claim.
    """

    def __init__(self, uid: str = "firebase-uid-1", email: str | None = None) -> None:
        self.uid = uid
        self.email = email
        self.seen: list[str] = []

    def verify(self, token: str) -> VerifiedSubject:
        self.seen.append(token)
        if token == VALID_TOKEN:
            return VerifiedSubject(uid=self.uid, email=self.email, email_verified=True)
        if token == EXPIRED_TOKEN:
            raise AuthenticationError("The ID token has expired.", code="TOKEN_EXPIRED")
        if token == REVOKED_TOKEN:
            raise AuthenticationError("The ID token has been revoked.", code="TOKEN_REVOKED")
        if token == UNREACHABLE_TOKEN:
            raise ServiceUnavailableError("Cannot verify credentials right now.")
        raise AuthenticationError("The ID token is invalid.", code="TOKEN_INVALID")


@pytest.fixture()
def firebase_provider(session, users):
    verifier = StubVerifier(uid=users["student"].firebase_uid)
    return FirebaseAuthProvider(lambda: session, verifier), verifier


@pytest.fixture()
def firebase_client(app, session, users):
    """The application, switched to the Firebase provider mid-test.

    Everything above the provider is untouched, which is the point of the seam:
    the routes, services and authorisation policy have no idea this happened.
    """
    app.config["AUTH_PROVIDER"] = "firebase"
    app.extensions["auth_provider"] = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["student"].firebase_uid)
    )
    return app.test_client()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1-2. Missing and malformed Authorization headers
# ---------------------------------------------------------------------------


def test_missing_authorization_header_is_401(firebase_client):
    response = firebase_client.get("/api/v1/locations")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "UNAUTHENTICATED"


def test_missing_header_is_not_an_error_for_the_provider(firebase_provider):
    """No credential is not a bad credential.

    Public endpoints need to tell "nobody asked" from "someone asked wrongly".
    """
    provider, _ = firebase_provider
    assert provider.extract_credential({}) is None
    assert provider.authenticate(None) is None


@pytest.mark.parametrize(
    "header",
    [
        "Basic dXNlcjpwYXNz",
        "Token abc.def.ghi",
        "abc.def.ghi",
        "Bearer",
        "Bearer ",
        "Bearer  two words",
        "   ",
    ],
)
def test_malformed_authorization_header_is_401(firebase_client, header):
    response = firebase_client.get("/api/v1/locations", headers={"Authorization": header})
    assert response.status_code == 401


def test_malformed_header_says_what_was_expected(firebase_provider):
    """A client sending the wrong scheme has made a fixable mistake."""
    provider, _ = firebase_provider
    with pytest.raises(AuthenticationError) as exc:
        provider.extract_credential({"Authorization": "Basic dXNlcjpwYXNz"})
    assert exc.value.code == "MALFORMED_AUTHORIZATION"
    assert "Bearer" in str(exc.value.details)


def test_bearer_scheme_is_case_insensitive(firebase_provider):
    """RFC 7235 makes the scheme case-insensitive; clients get this wrong."""
    provider, _ = firebase_provider
    for prefix in ("Bearer", "bearer", "BEARER", "BeArEr"):
        assert provider.extract_credential({"Authorization": f"{prefix} {VALID_TOKEN}"}) == (
            VALID_TOKEN
        )


# ---------------------------------------------------------------------------
# 3-4. Invalid and expired tokens
# ---------------------------------------------------------------------------


def test_invalid_token_is_401(firebase_client):
    response = firebase_client.get("/api/v1/locations", headers=bearer(INVALID_TOKEN))
    assert response.status_code == 401


def test_expired_token_is_401_and_distinguishable(firebase_client):
    """A client that cannot tell "expired" from "invalid" cannot know to refresh
    and retry, so it signs the user out instead."""
    response = firebase_client.get("/api/v1/locations", headers=bearer(EXPIRED_TOKEN))
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "TOKEN_EXPIRED"


def test_revoked_token_is_401(firebase_client):
    response = firebase_client.get("/api/v1/locations", headers=bearer(REVOKED_TOKEN))
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "TOKEN_REVOKED"


def test_unreachable_certificates_are_503_not_401(firebase_client):
    """Not an authentication failure.

    Telling a legitimate user their credentials are bad, when the truth is that
    we cannot reach Google to check, sends them to reset a password that was
    never the problem.
    """
    response = firebase_client.get("/api/v1/locations", headers=bearer(UNREACHABLE_TOKEN))
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 5-6. Valid token, and UID mapping
# ---------------------------------------------------------------------------


def test_valid_token_authenticates(firebase_client):
    response = firebase_client.get("/api/v1/locations", headers=bearer(VALID_TOKEN))
    assert response.status_code == 200


def test_valid_token_resolves_the_application_user(firebase_provider, users):
    provider, _ = firebase_provider
    principal = provider.authenticate(VALID_TOKEN)

    assert principal is not None
    assert principal.user_id == users["student"].user_id
    assert principal.email == users["student"].institutional_email


@pytest.mark.privacy
def test_principal_carries_the_postgres_id_not_the_firebase_uid(firebase_provider, users):
    """Requirement 6, and it is not cosmetic.

    Every foreign key in the schema — attribution, assignment, audit — points at
    ``identity.app_user.user_id``. Letting a Firebase UID stand in for it would
    put an external vendor's identifier into the middle of the data model, so
    changing identity provider would mean rewriting every table that references
    a person.
    """
    provider, _ = firebase_provider
    principal = provider.authenticate(VALID_TOKEN)

    assert isinstance(principal.user_id, uuid.UUID)
    assert str(principal.user_id) != users["student"].firebase_uid
    assert principal.user_id == users["student"].user_id


def test_unknown_firebase_uid_is_rejected(session, users):
    """The token verified, so the caller is a real Firebase user — with no
    account here."""
    provider = FirebaseAuthProvider(lambda: session, StubVerifier(uid="nobody-here"))
    with pytest.raises(AuthenticationError, match="No account exists"):
        provider.authenticate(VALID_TOKEN)


def test_unknown_uid_is_indistinguishable_from_a_bad_token(firebase_client, session, users):
    """Otherwise anyone holding any valid token for the project could enumerate
    which UIDs have accounts."""
    from app.security.providers import FirebaseAuthProvider as Provider

    unknown = Provider(lambda: session, StubVerifier(uid="nobody-here"))
    firebase_client.application.extensions["auth_provider"] = unknown

    unknown_uid = firebase_client.get("/api/v1/locations", headers=bearer(VALID_TOKEN))
    bad_token = firebase_client.get("/api/v1/locations", headers=bearer(INVALID_TOKEN))

    assert unknown_uid.status_code == bad_token.status_code == 401


def test_deactivated_account_is_rejected(session, users):
    users["student"].is_active = False
    session.flush()

    provider = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["student"].firebase_uid)
    )
    with pytest.raises(AuthenticationError, match="deactivated"):
        provider.authenticate(VALID_TOKEN)


# ---------------------------------------------------------------------------
# 7 & 11-13. The client controls neither identity nor role
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_role_comes_from_the_database_not_the_token(session, users):
    """Requirements 12 and 13.

    The stub returns the ICC member's UID. Whatever a token might claim, the role
    on the principal is the one in ``identity.app_user``.
    """
    provider = FirebaseAuthProvider(lambda: session, StubVerifier(uid=users["icc"].firebase_uid))
    principal = provider.authenticate(VALID_TOKEN)

    assert principal.role is UserRole.ICC
    assert principal.user_id == users["icc"].user_id


@pytest.mark.privacy
def test_changing_the_database_role_changes_the_principal(session, users):
    """The consequence of taking role from the row: a demotion takes effect on
    the next request, not when the user's current token happens to expire."""
    provider = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["student"].firebase_uid)
    )
    assert provider.authenticate(VALID_TOKEN).role is UserRole.STUDENT

    users["student"].role = UserRole.SECURITY
    session.flush()
    assert provider.authenticate(VALID_TOKEN).role is UserRole.SECURITY


@pytest.mark.privacy
def test_verified_subject_carries_no_role_or_claims():
    """Requirement 12, made structural.

    There is no claims dictionary to read a role out of. A future contributor
    looking for one finds nothing rather than finding something tempting.
    """
    subject = VerifiedSubject(uid="abc", email="x@example.com")
    assert not hasattr(subject, "claims")
    assert not hasattr(subject, "role")
    assert set(subject.__slots__) == {"uid", "email", "email_verified"}


@pytest.mark.privacy
def test_a_raw_uid_in_the_header_is_not_proof_of_identity(firebase_client, users):
    """Requirement 11.

    The most obvious attack on a UID-keyed system: send the UID and hope. It goes
    through verification like any other string and fails.
    """
    response = firebase_client.get(
        "/api/v1/locations", headers=bearer(users["student"].firebase_uid)
    )
    assert response.status_code == 401


@pytest.mark.privacy
def test_a_role_header_is_ignored(firebase_client, users):
    """Requirement 12. Nothing reads a role header; this asserts it stays that
    way."""
    response = firebase_client.get(
        "/api/v1/locations",
        headers={**bearer(VALID_TOKEN), "X-Role": "admin", "X-User-Role": "admin"},
    )
    assert response.status_code == 200


@pytest.mark.privacy
def test_dev_header_is_ignored_under_the_firebase_provider(firebase_client, users):
    """The development back door must close when Firebase is selected."""
    response = firebase_client.get(
        "/api/v1/locations", headers={"X-Dev-User": str(users["admin"].user_id)}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 8. The existing authorisation layer is unchanged
# ---------------------------------------------------------------------------


def _submit(client, payload, token=VALID_TOKEN):
    return client.post("/api/v1/reports", json=payload, headers=bearer(token))


def test_reports_can_be_filed_over_firebase_auth(firebase_client, report_payload):
    response = _submit(firebase_client, report_payload())
    assert response.status_code == 201
    assert response.get_json()["submission_mode"] == "identified"


@pytest.mark.privacy
def test_anonymous_reports_still_create_no_attribution(firebase_client, session, report_payload):
    """Requirement 10 in the brief: the anonymity guarantee is independent of how
    the caller authenticated."""
    from sqlalchemy import text

    ref = _submit(firebase_client, report_payload(anonymous=True)).get_json()["public_ref"]
    count = session.scalar(
        text(
            "SELECT count(*) FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert count == 0


@pytest.mark.privacy
def test_authority_routing_still_applies_over_firebase(app, session, users, report_payload):
    """Security still cannot read a report routed to the ICC. Changing how a
    caller proves who they are must not change what they may see."""
    app.config["AUTH_PROVIDER"] = "firebase"

    app.extensions["auth_provider"] = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["student"].firebase_uid)
    )
    client = app.test_client()
    ref = _submit(client, report_payload()).get_json()["public_ref"]

    app.extensions["auth_provider"] = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["security"].firebase_uid)
    )
    assert client.get(f"/api/v1/reports/{ref}", headers=bearer(VALID_TOKEN)).status_code == 404

    app.extensions["auth_provider"] = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["icc"].firebase_uid)
    )
    assert client.get(f"/api/v1/reports/{ref}", headers=bearer(VALID_TOKEN)).status_code == 200


@pytest.mark.privacy
def test_admin_narrative_firewall_holds_over_firebase(app, session, users, report_payload):
    app.config["AUTH_PROVIDER"] = "firebase"

    app.extensions["auth_provider"] = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["student"].firebase_uid)
    )
    client = app.test_client()
    ref = _submit(client, report_payload()).get_json()["public_ref"]

    app.extensions["auth_provider"] = FirebaseAuthProvider(
        lambda: session, StubVerifier(uid=users["admin"].firebase_uid)
    )
    body = client.get(f"/api/v1/reports/{ref}", headers=bearer(VALID_TOKEN)).get_json()

    assert body["narrative"] is None
    assert body["narrative_withheld_reason"] == "not_authorised"


@pytest.mark.privacy
def test_no_response_leaks_the_firebase_uid(firebase_client, users, report_payload):
    ref = _submit(firebase_client, report_payload()).get_json()["public_ref"]
    body = firebase_client.get(f"/api/v1/reports/{ref}", headers=bearer(VALID_TOKEN)).get_json()

    serialised = str(body)
    assert users["student"].firebase_uid not in serialised
    assert "firebase" not in serialised.lower()
    assert "user_id" not in serialised


# ---------------------------------------------------------------------------
# 11. Development authentication stays development-only
# ---------------------------------------------------------------------------


def test_both_providers_satisfy_the_protocol():
    """The substitution is checked, not assumed."""
    assert isinstance(DevAuthProvider(lambda: None), AuthProvider)
    assert isinstance(FirebaseAuthProvider(lambda: None, StubVerifier()), AuthProvider)


def test_stub_verifier_satisfies_the_verifier_protocol():
    assert isinstance(StubVerifier(), TokenVerifier)
    assert isinstance(
        FirebaseTokenVerifier(project_id="demo-project"),
        TokenVerifier,
    )


def test_production_refuses_the_development_provider():
    from app.config import ConfigError, ProductionConfig

    cfg = ProductionConfig()
    cfg.AUTH_PROVIDER = "dev"
    with pytest.raises(ConfigError, match="refused in production"):
        cfg.validate()


def test_firebase_requires_a_project_id():
    from app.config import ConfigError, DevelopmentConfig

    cfg = DevelopmentConfig()
    cfg.AUTH_PROVIDER = "firebase"
    cfg.FIREBASE_PROJECT_ID = None
    with pytest.raises(ConfigError, match="FIREBASE_PROJECT_ID"):
        cfg.validate()


def test_unknown_auth_provider_is_rejected_at_config_time():
    from app.config import ConfigError, DevelopmentConfig

    cfg = DevelopmentConfig()
    cfg.AUTH_PROVIDER = "oauth-something"
    with pytest.raises(ConfigError, match="unknown AUTH_PROVIDER"):
        cfg.validate()


def test_build_provider_rejects_an_unknown_name():
    with pytest.raises(RuntimeError, match="unknown AUTH_PROVIDER"):
        build_provider("saml", lambda: None)


def test_dev_provider_warns_loudly_when_built(caplog):
    """Every boot says so. A back door nobody is reminded about is a back door
    that gets forgotten."""
    import logging

    with caplog.at_level(logging.WARNING):
        build_provider("dev", lambda: None)
    assert any("DEVELOPMENT AUTHENTICATION ENABLED" in r.message for r in caplog.records)


def test_dev_provider_ignores_a_bearer_token(session, users):
    """Each provider reads only its own header, so the two cannot be mixed to
    smuggle a credential past whichever one is configured."""
    provider = DevAuthProvider(lambda: session)
    assert provider.extract_credential({"Authorization": f"Bearer {VALID_TOKEN}"}) is None


# ---------------------------------------------------------------------------
# The real verifier, without credentials
# ---------------------------------------------------------------------------


def test_real_verifier_requires_a_project_id():
    with pytest.raises(FirebaseConfigurationError, match="FIREBASE_PROJECT_ID"):
        FirebaseTokenVerifier(project_id="")


def test_real_verifier_rejects_an_empty_token_without_contacting_firebase():
    """Cheap rejection before any network call or SDK initialisation."""
    verifier = FirebaseTokenVerifier(project_id="demo-project")
    for empty in ("", "   "):
        with pytest.raises(AuthenticationError) as exc:
            verifier.verify(empty)
        assert exc.value.code == "TOKEN_MISSING"


def test_real_verifier_reports_a_missing_credentials_file():
    """The failure names the variable to fix rather than surfacing a stack trace
    from inside the SDK."""
    verifier = FirebaseTokenVerifier(
        project_id="demo-project", credentials_path="/nonexistent/service-account.json"
    )
    with pytest.raises(FirebaseConfigurationError, match="does not exist"):
        verifier.initialize()


def test_real_verifier_reports_malformed_inline_credentials():
    verifier = FirebaseTokenVerifier(project_id="demo-project", credentials_json="{not json")
    with pytest.raises(FirebaseConfigurationError, match="not valid JSON"):
        verifier.initialize()


def test_startup_does_not_probe_default_credentials_it_does_not_need():
    """ID token verification needs the project id and Google's public
    certificates — not the service-account key.

    Probing Application Default Credentials off Google infrastructure blocks on
    the metadata server at 169.254.169.254 for about nine seconds. Paying that on
    every boot, to warn about something verification does not use, is a bad
    trade. The assertion is on elapsed time because that is the actual defect: a
    slow start nobody would attribute to authentication.
    """
    import time

    started = time.monotonic()
    FirebaseTokenVerifier(project_id="campusshield-fast-start-test").initialize()
    assert time.monotonic() - started < 2.0


def test_missing_default_credentials_are_fatal_when_revocation_checking_is_on():
    """That path does call the Admin API, so it genuinely needs credentials."""
    verifier = FirebaseTokenVerifier(project_id="campusshield-revoke-test", check_revoked=True)
    with pytest.raises(FirebaseConfigurationError, match="FIREBASE_CHECK_REVOKED"):
        verifier.initialize()


@pytest.mark.privacy
def test_credential_configuration_errors_do_not_echo_the_secret():
    """FIREBASE_CREDENTIALS_JSON contains a private key. An error that quotes the
    offending value would put it in the logs."""
    secret = '{"private_key": "-----BEGIN PRIVATE KEY-----AAAA", broken'
    verifier = FirebaseTokenVerifier(project_id="demo-project", credentials_json=secret)
    with pytest.raises(FirebaseConfigurationError) as exc:
        verifier.initialize()
    assert "BEGIN PRIVATE KEY" not in str(exc.value)


def test_verify_does_not_probe_adc_when_check_revoked_is_off():
    """Verification without revocation checking verifies against public certificates
    and must not probe metadata servers or raise DefaultCredentialsError."""
    import time

    verifier = FirebaseTokenVerifier(project_id="campusshield-verify-no-adc")
    started = time.monotonic()
    with pytest.raises(AuthenticationError) as exc:
        verifier.verify("invalid.header.payload")
    assert exc.value.code == "TOKEN_INVALID"
    assert time.monotonic() - started < 3.0

