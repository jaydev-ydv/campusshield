"""In-memory storage provider, for tests and local development.

Real enough to exercise the whole evidence flow: bytes go in, bytes come out,
deletes remove them, and a missing object raises exactly as Firebase would.

Using it in a test mocks **only** the network boundary. Image sanitisation,
authorization, database constraints, and the anonymity guarantees all run for
real against it — which is the point of having a provider seam at all.

Selected with `STORAGE_PROVIDER=memory`. `ProductionConfig` refuses it.
"""

from __future__ import annotations

import threading

from .provider import StorageError, StorageObject


class InMemoryStorageProvider:
    name = "memory"

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def put(self, path: str, data: bytes, *, content_type: str) -> StorageObject:
        with self._lock:
            self._objects[path] = (data, content_type)
        return StorageObject(path=path, byte_size=len(data), content_type=content_type)

    def get(self, path: str) -> bytes:
        with self._lock:
            if path not in self._objects:
                raise StorageError("no object at the requested path")
            return self._objects[path][0]

    def delete(self, path: str) -> bool:
        with self._lock:
            return self._objects.pop(path, None) is not None

    def exists(self, path: str) -> bool:
        with self._lock:
            return path in self._objects

    # -- test helpers ------------------------------------------------------

    @property
    def stored_paths(self) -> list[str]:
        with self._lock:
            return sorted(self._objects)

    def clear(self) -> None:
        with self._lock:
            self._objects.clear()
