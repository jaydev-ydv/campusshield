"""Storage path generation.

One function, in one place, so that "the client cannot choose a path" is a
property of the system rather than a habit.

The path is a random UUID plus an extension. It contains:

* no Firebase UID
* no email address
* no report reference or report id
* no original filename
* no timestamp
* nothing derived from any of the above

Flat rather than nested under a report. Nesting would make one object's path
predictable from another's in the same report, and would tell anyone who saw a
path how many pieces of evidence a report has.
"""

from __future__ import annotations

import uuid

# Extension by sanitised content type. The extension is cosmetic — nothing reads
# it — but a path with no extension is awkward to inspect in a bucket console.
_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

PREFIX = "evidence"


def generate_storage_path(content_type: str) -> str:
    """A fresh, unpredictable, identity-free object path."""
    extension = _EXTENSIONS.get(content_type)
    if extension is None:
        raise ValueError(f"no storage path for unsupported content type {content_type!r}")
    return f"{PREFIX}/{uuid.uuid4().hex}.{extension}"


def is_server_generated(path: str) -> bool:
    """Whether a path has the shape this module produces.

    Used by the tests that prove a client-supplied path is never honoured.
    """
    if not path.startswith(f"{PREFIX}/"):
        return False
    remainder = path[len(PREFIX) + 1 :]
    if "/" in remainder or ".." in remainder:
        return False
    stem, _, extension = remainder.partition(".")
    if extension not in set(_EXTENSIONS.values()):
        return False
    return len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)
