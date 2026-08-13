"""Firebase Cloud Storage provider.

**The only module in the codebase that imports Firebase Storage.** Everything
above it works against the `StorageProvider` protocol, which is what makes the
implementation replaceable and lets tests run without a bucket.

Implemented against the installed versions — `firebase-admin` 7.5 and
`google-cloud-storage` 3.13 — rather than from memory. `storage.bucket(name,
app)` returns a `google.cloud.storage.Bucket`, and the blob operations used here
(`upload_from_string`, `download_as_bytes`, `delete`, `exists`) are all present
in that version.

The Admin SDK bypasses Storage Security Rules, which is the whole design: the
bucket denies every client (`allow read, write: if false`) and only this process
can touch it.

Reuses the Firebase app initialised by `app.security.firebase` rather than
creating a second one — one credential configuration, not two.
"""

from __future__ import annotations

import logging
from typing import Any

from .provider import StorageError, StorageObject, StorageUnavailableError

logger = logging.getLogger(__name__)


class FirebaseStorageProvider:
    """Sanitised evidence in a private Firebase Storage bucket."""

    name = "firebase_storage"

    def __init__(self, *, bucket_name: str, app: Any = None) -> None:
        if not bucket_name:
            raise StorageError(
                "FIREBASE_STORAGE_BUCKET is required when STORAGE_PROVIDER=firebase."
            )
        self._bucket_name = bucket_name
        self._app = app
        self._bucket: Any = None

    def _get_bucket(self) -> Any:
        """Resolve the bucket lazily, so importing this module needs no network."""
        if self._bucket is not None:
            return self._bucket
        try:
            from firebase_admin import storage as firebase_storage
        except ImportError as exc:  # pragma: no cover - dependency present in this project
            raise StorageError(
                "firebase-admin is not installed. Run: pip install -r backend/requirements.txt"
            ) from exc

        try:
            self._bucket = firebase_storage.bucket(self._bucket_name, app=self._app)
        except Exception as exc:
            raise StorageUnavailableError(
                f"could not open Firebase Storage bucket {self._bucket_name!r}: {exc}"
            ) from exc
        return self._bucket

    # -- StorageProvider ---------------------------------------------------

    def put(self, path: str, data: bytes, *, content_type: str) -> StorageObject:
        blob = self._get_bucket().blob(path)
        try:
            blob.upload_from_string(data, content_type=content_type)
        except Exception as exc:
            # Deliberately not logging `path` at error level alongside the
            # exception text: a storage exception can echo the full object URL,
            # and a URL in a log is a URL that outlives its authorisation check.
            logger.error("evidence upload to storage failed: %s", type(exc).__name__)
            raise StorageUnavailableError("could not store the uploaded image") from exc
        return StorageObject(path=path, byte_size=len(data), content_type=content_type)

    def get(self, path: str) -> bytes:
        blob = self._get_bucket().blob(path)
        try:
            return blob.download_as_bytes()
        except Exception as exc:
            raise StorageError(f"could not read object: {type(exc).__name__}") from exc

    def delete(self, path: str) -> bool:
        blob = self._get_bucket().blob(path)
        try:
            blob.delete()
            return True
        except Exception as exc:
            # Missing is success for a cleanup path. A reaper that raises on an
            # object someone already removed is a reaper that stops running.
            logger.info("storage delete found nothing to remove (%s)", type(exc).__name__)
            return False

    def exists(self, path: str) -> bool:
        try:
            return bool(self._get_bucket().blob(path).exists())
        except Exception:
            return False
