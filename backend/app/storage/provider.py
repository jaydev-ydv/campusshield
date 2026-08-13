"""The storage contract.

Four operations. Everything the evidence flow needs and nothing more — a
provider that cannot list, cannot enumerate, and cannot mint a public URL is a
provider that cannot leak a bucket.

Note what is absent: there is no ``public_url()``. Evidence is served by
streaming through an authorised endpoint, never by handing a URL to a client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class StorageError(RuntimeError):
    """The storage layer could not complete an operation."""


class StorageUnavailableError(StorageError):
    """The backing store could not be reached.

    Distinct from a rejected object: this is a dependency failure, and the
    caller should surface it as 503 rather than telling a student their image
    was invalid.
    """


@dataclass(frozen=True, slots=True)
class StorageObject:
    """A stored object, as the application sees it."""

    path: str
    byte_size: int
    content_type: str


@runtime_checkable
class StorageProvider(Protocol):
    """Where sanitised evidence bytes live."""

    name: str

    def put(self, path: str, data: bytes, *, content_type: str) -> StorageObject:
        """Store ``data`` at ``path``.

        ``path`` is always server-generated. A provider must never derive a path
        itself, so that path generation stays in one auditable place.
        """
        ...

    def get(self, path: str) -> bytes:
        """Read an object back. Raises :class:`StorageError` if absent."""
        ...

    def delete(self, path: str) -> bool:
        """Remove an object. Returns False if it was already gone.

        Must not raise for a missing object: cleanup runs after failures, and a
        reaper that crashes on an object someone already deleted is a reaper
        that stops reaping.
        """
        ...

    def exists(self, path: str) -> bool: ...
