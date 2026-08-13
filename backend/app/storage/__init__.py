"""Object storage, behind a provider boundary.

Nothing above this package imports ``firebase_admin.storage`` or
``google.cloud.storage``. The evidence service works against
:class:`~app.storage.provider.StorageProvider`, which is why the Firebase
implementation can be swapped for an in-memory one in tests without stubbing
anything the application actually owns.
"""

from .provider import (
    StorageError,
    StorageObject,
    StorageProvider,
    StorageUnavailableError,
)

__all__ = [
    "StorageError",
    "StorageObject",
    "StorageProvider",
    "StorageUnavailableError",
]
