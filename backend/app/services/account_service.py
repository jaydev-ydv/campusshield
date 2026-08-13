"""Account provisioning.

Bridges the gap between "has a Firebase account" and "has an application
account". Firebase owns credentials; ``identity.app_user`` owns identity and
role. Signing up with Firebase creates the first and not the second, so without
this every newly registered user would hold a perfectly valid token and get 401
forever.

The one rule this module exists to enforce: **the role is assigned here, by the
server, and is always `student`.** It is not read from the request body, not read
from a token claim, and not settable by any client. Authority accounts
(`security`, `icc`, `admin`) are created out of band by someone with database
access, because being on the ICC is an institutional fact and not something a
sign-up form can assert.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AuthorizationError, ConflictError, ValidationError
from ..models import AppUser
from ..models.enums import UserRole
from ..security.firebase import VerifiedSubject
from ..security.principal import Principal

logger = logging.getLogger(__name__)

# The role every self-provisioned account receives. Not a parameter, not a
# default that something could override — a constant, so that granting an
# authority role always requires touching the database deliberately.
SELF_PROVISIONED_ROLE = UserRole.STUDENT

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def provision(
        self, subject: VerifiedSubject, *, fallback_email: str | None = None
    ) -> tuple[Principal, bool]:
        """Create the application account for a verified external subject.

        Returns ``(principal, created)``. Idempotent: a second call for the same
        subject returns the existing account rather than failing, because a
        client that retries a request whose response was lost should not be told
        it already exists when from its point of view nothing happened.
        """
        existing = self._session.scalar(select(AppUser).where(AppUser.firebase_uid == subject.uid))
        if existing is not None:
            if not existing.is_active:
                raise ConflictError("This account is deactivated.")
            return self._principal(existing), False

        # The verified token's email wins whenever it has one. A client-supplied
        # address is only consulted when the credential carries none — which in
        # practice means the development provider. Letting a request body
        # override a verified email would allow one user to claim another's
        # address, and email is how an institution recognises its own people.
        email = subject.email or fallback_email
        if not email or not _EMAIL.match(email):
            raise ValidationError(
                "A valid email address is required to create an account.",
                details={"fields": {"email": ["Missing or malformed."]}},
            )

        clash = self._session.scalar(
            select(AppUser).where(AppUser.institutional_email.ilike(email))
        )
        if clash is not None:
            # A different Firebase identity already holds this address. Refusing
            # is the safe answer: silently attaching the new credential to the
            # existing account would be an account takeover.
            raise ConflictError("An account already exists for this email address.")

        user = AppUser(
            firebase_uid=subject.uid,
            role=SELF_PROVISIONED_ROLE,
            institutional_email=email,
            # Students have no display_name — a CHECK constraint enforces it.
            # Nothing in the project needs a student's name, and a field not
            # collected cannot be breached.
            display_name=None,
        )
        self._session.add(user)
        self._session.flush()

        logger.info("provisioned a new %s account", SELF_PROVISIONED_ROLE.value)
        return self._principal(user), True

    def update_display_name(self, principal: Principal, display_name: str) -> Principal:
        """Set the caller's own display name. Authority roles only.

        `ck_app_user_student_has_no_name` exists so that a field never collected
        from a student cannot later be breached. Refusing here, before the
        write, gives a student a clear "your role doesn't have this" answer
        instead of a raw constraint-violation 409 from the database.
        """
        if principal.role is UserRole.STUDENT:
            raise AuthorizationError(
                "Student accounts do not have a display name. Reports and "
                "profiles stay unnamed by design."
            )

        user = self._session.get(AppUser, principal.user_id)
        if user is None:  # pragma: no cover - an authenticated caller always has a row
            raise AuthorizationError("Account not found.")

        user.display_name = display_name
        self._session.flush()
        return self._principal(user)

    @staticmethod
    def _principal(user: AppUser) -> Principal:
        return Principal(
            user_id=user.user_id,
            role=user.role,
            email=user.institutional_email,
            is_active=user.is_active,
            display_name=user.display_name,
        )
