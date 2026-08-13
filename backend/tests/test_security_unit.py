"""Unit tests for the authorisation policy and auth abstraction.

No database, no Flask. The policy is pure functions over a context object
precisely so it can be tested like this — every row-level access decision in the
system is a call into these functions, and reading them here shows the whole
rule set in one place.
"""

from __future__ import annotations

import uuid

import pytest

from app.errors import AuthenticationError
from app.models.enums import UserRole
from app.security.authorization import (
    ReportAccessContext,
    can_create_report,
    can_resolve_identity,
    can_view_narrative,
    can_view_report,
)
from app.security.principal import Principal
from app.security.providers import AuthProvider, FirebaseAuthProvider

REPORTER = uuid.uuid4()
STRANGER = uuid.uuid4()
OFFICER = uuid.uuid4()


def principal(role: UserRole, user_id: uuid.UUID | None = None) -> Principal:
    return Principal(user_id=user_id or uuid.uuid4(), role=role, email="person@test.local")


def context(**overrides) -> ReportAccessContext:
    base = {
        "report_id": uuid.uuid4(),
        "routes_to_role": UserRole.ICC,
        "requires_confidentiality": True,
        "reporter_user_id": REPORTER,
        "assigned_to_user_id": None,
    }
    base.update(overrides)
    return ReportAccessContext(**base)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_authenticated_users_may_report():
    assert can_create_report(principal(UserRole.STUDENT)) is True


def test_staff_may_also_report():
    """A warden who witnesses something files through the same channel;
    `reporter_relationship` records the vantage point."""
    assert can_create_report(principal(UserRole.SECURITY)) is True


def test_anonymous_callers_may_not_report():
    assert can_create_report(None) is False


def test_deactivated_accounts_may_not_report():
    dead = Principal(
        user_id=uuid.uuid4(), role=UserRole.STUDENT, email="x@test.local", is_active=False
    )
    assert can_create_report(dead) is False


# ---------------------------------------------------------------------------
# Viewing
# ---------------------------------------------------------------------------


def test_reporter_sees_own_report():
    assert can_view_report(principal(UserRole.STUDENT, REPORTER), context()) is True


def test_other_student_sees_nothing():
    assert can_view_report(principal(UserRole.STUDENT, STRANGER), context()) is False


def test_unauthenticated_sees_nothing():
    assert can_view_report(None, context()) is False


def test_token_holder_sees_report():
    """Possession of the one-time token is the authorisation; for an anonymous
    report there is no identity to check it against."""
    ctx = context(reporter_user_id=None, accessed_via_token=True)
    assert can_view_report(None, ctx) is True
    assert can_view_narrative(None, ctx) is True


def test_icc_sees_reports_routed_to_icc():
    assert can_view_report(principal(UserRole.ICC), context()) is True


def test_security_does_not_see_icc_reports():
    """The rule that stops "authority access" becoming "everyone sees
    everything"."""
    assert can_view_report(principal(UserRole.SECURITY), context()) is False


def test_security_sees_reports_routed_to_security():
    ctx = context(routes_to_role=UserRole.SECURITY, requires_confidentiality=False)
    assert can_view_report(principal(UserRole.SECURITY), ctx) is True


def test_assigned_officer_sees_report_regardless_of_routing():
    ctx = context(routes_to_role=UserRole.ICC, assigned_to_user_id=OFFICER)
    assert can_view_report(principal(UserRole.SECURITY, OFFICER), ctx) is True


def test_report_with_no_category_is_not_visible_by_role_alone():
    """`routes_to_role` is None means no role-based route exists. Falling open
    here would make an uncategorised report visible to every authority."""
    ctx = context(routes_to_role=None)
    assert can_view_report(principal(UserRole.ICC), ctx) is False
    assert can_view_report(principal(UserRole.SECURITY), ctx) is False


# ---------------------------------------------------------------------------
# The narrative firewall
# ---------------------------------------------------------------------------


def test_admin_sees_metadata_but_not_narrative():
    admin = principal(UserRole.ADMIN)
    assert can_view_report(admin, context()) is True
    assert can_view_narrative(admin, context()) is False


def test_confidential_narrative_is_icc_only():
    ctx = context(routes_to_role=UserRole.SECURITY, requires_confidentiality=True)
    assert can_view_report(principal(UserRole.SECURITY), ctx) is True
    assert can_view_narrative(principal(UserRole.SECURITY), ctx) is False
    assert can_view_narrative(principal(UserRole.ICC), context()) is True


def test_reporter_always_reads_their_own_narrative():
    assert can_view_narrative(principal(UserRole.STUDENT, REPORTER), context()) is True


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


def test_identity_resolution_is_icc_only():
    assert can_resolve_identity(principal(UserRole.ICC), context()) is True
    assert can_resolve_identity(principal(UserRole.ADMIN), context()) is False
    assert can_resolve_identity(principal(UserRole.SECURITY), context()) is False
    assert can_resolve_identity(principal(UserRole.STUDENT, REPORTER), context()) is False


def test_anonymous_reports_have_no_identity_to_resolve():
    """Not a permission failure — there is nothing to return."""
    ctx = context(reporter_user_id=None)
    assert can_resolve_identity(principal(UserRole.ICC), ctx) is False


# ---------------------------------------------------------------------------
# The authentication seam
# ---------------------------------------------------------------------------


def test_providers_satisfy_the_protocol():
    """Swapping dev for Firebase is a configuration change, not a refactor.

    Firebase-specific behaviour is exercised in test_firebase_auth.py; this only
    asserts the seam still holds.
    """
    from app.security.firebase import FirebaseTokenVerifier
    from app.security.providers import DevAuthProvider

    assert isinstance(DevAuthProvider(lambda: None), AuthProvider)
    assert isinstance(
        FirebaseAuthProvider(lambda: None, FirebaseTokenVerifier(project_id="demo")),
        AuthProvider,
    )


def test_principal_repr_does_not_leak_email():
    """Reprs reach logs and tracebacks, and a principal is on every request."""
    text = repr(principal(UserRole.STUDENT))
    assert "person@test.local" not in text
    assert "student" in text


def test_dev_provider_returns_none_without_a_credential():
    """No credential is not an authentication failure — public endpoints need
    to distinguish 'nobody asked' from 'bad credential'."""
    from app.security.providers import DevAuthProvider

    assert DevAuthProvider(lambda: None).authenticate(None) is None


def test_production_config_refuses_dev_authentication():
    from app.config import ConfigError, ProductionConfig

    cfg = ProductionConfig()
    cfg.AUTH_PROVIDER = "dev"
    with pytest.raises(ConfigError, match="refused in production"):
        cfg.validate()


def test_production_config_requires_secrets():
    from app.config import ConfigError, ProductionConfig

    cfg = ProductionConfig()
    cfg.AUTH_PROVIDER = "firebase"
    # Supplied so validation reaches the secrets check; a missing project id, an
    # unreviewed CORS list, and in-memory evidence storage are their own errors,
    # asserted separately.
    cfg.FIREBASE_PROJECT_ID = "demo-project"
    cfg.STORAGE_PROVIDER = "firebase"
    cfg.FIREBASE_STORAGE_BUCKET = "demo-project.appspot.com"
    cfg.CORS_ORIGINS = ["https://campusshield.example.edu"]
    with pytest.raises(ConfigError, match="missing required secrets"):
        cfg.validate()


def test_production_refuses_a_wildcard_cors_origin():
    """A wildcard on a credentialed API lets any site a student visits act as
    them."""
    from app.config import ConfigError, ProductionConfig

    cfg = ProductionConfig()
    cfg.AUTH_PROVIDER = "firebase"
    cfg.FIREBASE_PROJECT_ID = "demo-project"
    cfg.STORAGE_PROVIDER = "firebase"
    cfg.FIREBASE_STORAGE_BUCKET = "demo-project.appspot.com"
    cfg.CORS_ORIGINS = ["*"]
    with pytest.raises(ConfigError, match="CORS_ORIGINS"):
        cfg.validate()


def test_production_refuses_a_localhost_cors_origin():
    """A localhost entry surviving into production means the list was copied
    from the dev template and never reviewed."""
    from app.config import ConfigError, ProductionConfig

    cfg = ProductionConfig()
    cfg.AUTH_PROVIDER = "firebase"
    cfg.FIREBASE_PROJECT_ID = "demo-project"
    cfg.STORAGE_PROVIDER = "firebase"
    cfg.FIREBASE_STORAGE_BUCKET = "demo-project.appspot.com"
    cfg.CORS_ORIGINS = ["https://campusshield.example.edu", "http://localhost:5173"]
    with pytest.raises(ConfigError, match="CORS_ORIGINS"):
        cfg.validate()


def test_auth_error_is_distinct_from_no_credential():
    assert issubclass(AuthenticationError, Exception)
