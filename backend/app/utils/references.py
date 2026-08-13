"""Public references, access tokens, and hashing helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

# Ambiguous glyphs removed: someone will read a case reference off a screen and
# type it into a status form, and O/0 and I/1 are where that goes wrong.  Still
# within the schema's ^CS-[0-9]{4}-[A-Z0-9]{6}$ pattern.
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_REF_LENGTH = 6

TOKEN_BYTES = 16  # 128 bits


def generate_public_ref(now: datetime) -> str:
    """``CS-2026-7QK4M2`` — random, never sequential.

    A sequence would leak total report volume and submission ordering to anyone
    holding two references.
    """
    suffix = "".join(secrets.choice(_REF_ALPHABET) for _ in range(_REF_LENGTH))
    return f"CS-{now.year:04d}-{suffix}"


def generate_access_token() -> str:
    """Raw anonymous status-lookup token.  Shown once, never stored."""
    return secrets.token_hex(TOKEN_BYTES)


def hash_access_token(token: str) -> str:
    """SHA-256 of the raw token; this is what the database keeps.

    Unpeppered on purpose.  The token is 128 bits of entropy, so a dictionary
    attack is not the threat model, and keeping the hash a pure function of the
    token means rotating a server pepper cannot lock every anonymous reporter out
    of their own case.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_client_ip(ip: str | None, pepper: str | None) -> str | None:
    """Keyed hash of a client address for ``audit.access_log.ip_hash``.

    Enough to recognise "same client" across requests, useless for locating
    anybody.  Without a pepper the value is dropped rather than stored
    unkeyed — a bare SHA-256 of an IPv4 address is trivially reversible by
    enumerating the whole space.
    """
    if not ip or not pepper:
        return None
    return hmac.new(pepper.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_user_agent(user_agent: str | None, pepper: str | None) -> str | None:
    if not user_agent or not pepper:
        return None
    return hmac.new(pepper.encode("utf-8"), user_agent.encode("utf-8"), hashlib.sha256).hexdigest()
