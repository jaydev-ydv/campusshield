"""The authenticated caller, as the rest of the application sees it.

``Principal`` is the *only* thing an authentication provider returns.  Nothing
downstream — services, repositories, authorisation — knows whether the identity
came from a development header, a Firebase ID token, or something not yet
invented.  That is what makes the provider swappable.

Note what is absent: no token, no claims dictionary, no provider handle.  If a
service could reach the raw credential it would eventually be tempted to
re-verify or forward it, and the abstraction would leak.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..models.enums import UserRole


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated user."""

    user_id: uuid.UUID
    role: UserRole
    email: str
    is_active: bool = True
    # None for every student, by the same `ck_app_user_student_has_no_name`
    # constraint that keeps `identity.app_user.display_name` unset for them.
    # Present, and settable, only for authority roles.
    display_name: str | None = None

    @property
    def is_student(self) -> bool:
        return self.role is UserRole.STUDENT

    @property
    def is_authority(self) -> bool:
        return self.role is not UserRole.STUDENT

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Email is deliberately not shown: reprs end up in logs and exception
        # tracebacks, and a principal is attached to every request.
        return f"<Principal {self.user_id} role={self.role.value}>"
