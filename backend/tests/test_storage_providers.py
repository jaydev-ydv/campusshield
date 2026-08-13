"""The storage provider seam — `FirebaseStorageProvider` unit tests, and the
app-factory wiring that decides which Firebase Admin app it uses.

**Only `firebase_admin.storage.bucket` is mocked** for the provider tests:
everything downstream of it — path building, exception wrapping, the app
object actually handed to the SDK — is the real class. The wiring tests below
that call `create_app()` mock nothing at all; `firebase_admin.initialize_app`
resolves ID-token verification and bucket lookups lazily, so constructing an
app with fake project ids and no credentials is fast and network-free, exactly
as `test_firebase_auth.py::test_startup_does_not_probe_default_credentials_it_does_not_need`
already established for the auth path alone.

The central thing under test here is a defect this phase found and fixed:
`_build_storage_provider` used to construct `FirebaseStorageProvider` without
passing `app=`, so it would resolve the Firebase Admin SDK's global default
app — an app this codebase never initialises under that name, since
authentication always uses a named one (`campusshield-<project id>`). In any
real deployment with `STORAGE_PROVIDER=firebase`, the very first storage
operation would have raised. `test_storage_reuses_the_same_firebase_app_as_auth`
and `test_storage_initialises_its_own_app_when_auth_is_not_firebase` are the
regression tests for that fix.
"""

from __future__ import annotations

import logging

import pytest

from app.storage.firebase_provider import FirebaseStorageProvider
from app.storage.paths import PREFIX, generate_storage_path, is_server_generated
from app.storage.provider import StorageError, StorageObject, StorageUnavailableError

# ---------------------------------------------------------------------------
# Fakes — the network boundary only
# ---------------------------------------------------------------------------


class _FakeBlob:
    def __init__(self, path, store, fail):
        self._path = path
        self._store = store
        self._fail = fail

    def upload_from_string(self, data, content_type=None):
        if "put" in self._fail:
            raise self._fail["put"]
        self._store[self._path] = (data, content_type)

    def download_as_bytes(self):
        if "get" in self._fail:
            raise self._fail["get"]
        if self._path not in self._store:
            raise LookupError("no such object")
        return self._store[self._path][0]

    def delete(self):
        if "delete" in self._fail:
            raise self._fail["delete"]
        if self._path not in self._store:
            raise LookupError("no such object")
        del self._store[self._path]

    def exists(self):
        if "exists" in self._fail:
            raise self._fail["exists"]
        return self._path in self._store


class _FakeBucket:
    def __init__(self, name, app, store, fail):
        self.name = name
        self.app = app
        self._store = store
        self._fail = fail

    def blob(self, path):
        return _FakeBlob(path, self._store, self._fail)


def _install_fake_bucket(monkeypatch, *, store=None, fail=None, open_error=None):
    """Patch firebase_admin.storage.bucket, recording every call it receives.

    Returns (store, calls). `calls` is a list of (bucket_name, app) tuples, so a
    test can assert exactly which app object a bucket lookup resolved against —
    without a real Firebase Admin app.
    """
    import firebase_admin.storage as firebase_storage_module

    store = {} if store is None else store
    fail = {} if fail is None else fail
    calls: list[tuple[str, object]] = []

    def fake_bucket(name, app=None):
        calls.append((name, app))
        if open_error is not None:
            raise open_error
        return _FakeBucket(name, app, store, fail)

    monkeypatch.setattr(firebase_storage_module, "bucket", fake_bucket)
    return store, calls


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_a_bucket_name_is_required():
    with pytest.raises(StorageError, match="FIREBASE_STORAGE_BUCKET"):
        FirebaseStorageProvider(bucket_name="")


def test_provider_has_no_public_url_method():
    """Structural: nothing on this class can hand a client a bucket URL.
    Evidence is always streamed back through an authorised endpoint."""
    assert not hasattr(FirebaseStorageProvider, "public_url")


# ---------------------------------------------------------------------------
# Bucket resolution must use the app it was given, not the SDK's default
# ---------------------------------------------------------------------------


def test_bucket_resolution_uses_the_app_passed_to_the_constructor(monkeypatch):
    """The regression test for the app-reuse defect: a provider constructed
    with an explicit app must hand that exact object to
    firebase_admin.storage.bucket, never the SDK's default app."""
    _, calls = _install_fake_bucket(monkeypatch)
    sentinel_app = object()
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=sentinel_app)

    provider.exists("evidence/x.jpg")

    assert calls == [("a-bucket", sentinel_app)]


def test_bucket_is_resolved_once_and_cached(monkeypatch):
    _, calls = _install_fake_bucket(monkeypatch)
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())

    provider.exists("evidence/a.jpg")
    provider.exists("evidence/b.jpg")

    assert len(calls) == 1, "the bucket must be looked up once, not per operation"


# ---------------------------------------------------------------------------
# put / get / delete / exists, against the real class
# ---------------------------------------------------------------------------


def test_put_then_get_round_trips_the_exact_bytes(monkeypatch):
    _install_fake_bucket(monkeypatch)
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())
    path = generate_storage_path("image/jpeg")

    result = provider.put(path, b"the sanitised bytes", content_type="image/jpeg")

    assert result == StorageObject(path=path, byte_size=19, content_type="image/jpeg")
    assert provider.get(path) == b"the sanitised bytes"


def test_exists_is_true_after_put_and_false_after_delete(monkeypatch):
    _install_fake_bucket(monkeypatch)
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())
    path = generate_storage_path("image/png")

    assert provider.exists(path) is False
    provider.put(path, b"x", content_type="image/png")
    assert provider.exists(path) is True
    assert provider.delete(path) is True
    assert provider.exists(path) is False


def test_delete_of_a_missing_object_returns_false_without_raising(monkeypatch):
    """Cleanup must be idempotent: a reaper that raises on an object someone
    already removed is a reaper that stops reaping."""
    _install_fake_bucket(monkeypatch)
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())

    assert provider.delete("evidence/never-existed.jpg") is False


def test_get_of_a_missing_object_raises_storage_error(monkeypatch):
    _install_fake_bucket(monkeypatch)
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())

    with pytest.raises(StorageError):
        provider.get("evidence/never-existed.jpg")


def test_exists_returns_false_rather_than_raising_on_a_backend_error(monkeypatch):
    _install_fake_bucket(monkeypatch, fail={"exists": RuntimeError("network blip")})
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())

    assert provider.exists("evidence/x.jpg") is False


# ---------------------------------------------------------------------------
# Failure handling and error wrapping
# ---------------------------------------------------------------------------


def test_a_failed_bucket_open_raises_storage_unavailable(monkeypatch):
    _install_fake_bucket(monkeypatch, open_error=RuntimeError("bucket unreachable"))
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())

    with pytest.raises(StorageUnavailableError):
        provider.put("evidence/x.jpg", b"data", content_type="image/jpeg")


def test_a_failed_upload_raises_storage_unavailable_not_storage_error(monkeypatch):
    """Specifically StorageUnavailableError: a dependency failure must surface
    as 503, not as 'your image was invalid.'"""
    _install_fake_bucket(monkeypatch, fail={"put": RuntimeError("quota exceeded")})
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())

    with pytest.raises(StorageUnavailableError):
        provider.put("evidence/x.jpg", b"data", content_type="image/jpeg")


@pytest.mark.privacy
def test_a_storage_exception_is_not_logged_alongside_the_path(monkeypatch, caplog):
    """A storage exception can echo the full object URL. Logging that text next
    to the path at error level would put a sensitive URL in the log stream."""
    leaky = RuntimeError(
        "https://storage.googleapis.com/a-bucket/evidence/super-secret-path.jpg?sig=abc"
    )
    _install_fake_bucket(monkeypatch, fail={"put": leaky})
    provider = FirebaseStorageProvider(bucket_name="a-bucket", app=object())
    path = "evidence/super-secret-path.jpg"

    with caplog.at_level(logging.ERROR), pytest.raises(StorageUnavailableError):
        provider.put(path, b"data", content_type="image/jpeg")

    for record in caplog.records:
        assert path not in record.getMessage()
        assert "sig=abc" not in record.getMessage()


# ---------------------------------------------------------------------------
# Path opacity — is_server_generated as its own unit, not just an assertion
# helper
# ---------------------------------------------------------------------------


def test_is_server_generated_accepts_its_own_output():
    for content_type in ("image/jpeg", "image/png", "image/webp"):
        assert is_server_generated(generate_storage_path(content_type)) is True


@pytest.mark.privacy
@pytest.mark.parametrize(
    "hostile",
    [
        "../../another-report/image.jpg",
        "evidence/../../../secrets",
        "/etc/passwd",
        "evidence/x/y.jpg",
        "evidence/not-hex-not-32-chars-long-enough.jpg",
        f"{PREFIX}/" + "a" * 32,  # right shape, no extension
        f"{PREFIX}/" + "a" * 31 + ".jpg",  # one character short
        f"{PREFIX}/" + "g" * 32 + ".jpg",  # not hex
        "other-prefix/" + "a" * 32 + ".jpg",
        "",
    ],
)
def test_is_server_generated_rejects_non_conforming_paths(hostile):
    assert is_server_generated(hostile) is False


# ---------------------------------------------------------------------------
# App-factory wiring — the defect this phase found and fixed
# ---------------------------------------------------------------------------


def _firebase_config(database_url: str, *, auth_provider: str, project_id: str):
    from app.config import TestingConfig

    cfg = TestingConfig()
    cfg.SQLALCHEMY_DATABASE_URI = database_url
    cfg.AUTH_PROVIDER = auth_provider
    cfg.FIREBASE_PROJECT_ID = project_id
    cfg.STORAGE_PROVIDER = "firebase"
    cfg.FIREBASE_STORAGE_BUCKET = f"{project_id}.appspot.com"
    cfg.AUDIT_IP_PEPPER = "test-pepper"
    cfg.REPORT_TOKEN_PEPPER = "test-pepper"
    return cfg


def test_storage_reuses_the_same_firebase_app_as_auth(database_url):
    """When both AUTH_PROVIDER and STORAGE_PROVIDER are firebase, they must end
    up sharing one Firebase Admin app — one credential configuration, not two,
    as app.storage.firebase_provider's own docstring promises. Before this
    phase's fix, storage always resolved app=None instead."""
    from app import create_app

    cfg = _firebase_config(
        database_url, auth_provider="firebase", project_id="campusshield-storage-wiring-shared"
    )
    flask_app = create_app(cfg)

    auth_app = flask_app.extensions["auth_provider"]._verifier.app
    storage_app = flask_app.extensions["storage_provider"]._app

    assert storage_app is auth_app
    assert storage_app.name == f"campusshield-{cfg.FIREBASE_PROJECT_ID}"


def test_storage_initialises_its_own_app_when_auth_is_not_firebase(database_url):
    """dev auth with real Firebase Storage is a legitimate combination — local
    development against a real bucket without minting Firebase ID tokens. The
    storage provider must not be left with app=None in that case either."""
    from app import create_app

    cfg = _firebase_config(
        database_url, auth_provider="dev", project_id="campusshield-storage-wiring-standalone"
    )
    flask_app = create_app(cfg)

    storage_app = flask_app.extensions["storage_provider"]._app
    assert storage_app is not None
    assert storage_app.name == f"campusshield-{cfg.FIREBASE_PROJECT_ID}"


def test_storage_provider_requires_a_project_id_even_without_firebase_auth():
    from app.config import ConfigError, DevelopmentConfig

    cfg = DevelopmentConfig()
    cfg.AUTH_PROVIDER = "dev"
    cfg.STORAGE_PROVIDER = "firebase"
    cfg.FIREBASE_STORAGE_BUCKET = "some-bucket.appspot.com"
    cfg.FIREBASE_PROJECT_ID = None
    with pytest.raises(ConfigError, match="FIREBASE_PROJECT_ID"):
        cfg.validate()
