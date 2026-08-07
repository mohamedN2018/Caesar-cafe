"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.authz.models import Role, RoleAssignment
from apps.authz.services import ensure_system_roles
from apps.organizations.models import Branch, Organization

# NOTE: the suite ends with one PytestWarning — Postgres refuses to drop the
# test database because a Channels `database_sync_to_async` worker thread still
# holds a session. It is a teardown artifact of running async consumers under
# pytest, not a product defect: every test passes and the database is recreated
# on the next run. Two attempts to close those threads from a fixture did not
# reach them, so the warning is left visible rather than papered over.


@pytest.fixture(autouse=True)
def _clear_caches():
    """
    Permission sets and settings both cache per scope; leaking that between
    tests produces order-dependent failures that are miserable to debug.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def organization(db) -> Organization:
    return Organization.objects.create(name_ar="كافيه القيصر", name_en="Caesar Cafe")


@pytest.fixture
def branch(organization: Organization) -> Branch:
    return Branch.objects.create(organization=organization, code="MB", name_ar="الفرع الرئيسي")


@pytest.fixture
def other_organization(db) -> Organization:
    return Organization.objects.create(name_ar="كافيه آخر", name_en="Other Cafe")


@pytest.fixture
def other_branch(other_organization: Organization) -> Branch:
    return Branch.objects.create(organization=other_organization, code="OB", name_ar="فرع آخر")


@pytest.fixture
def roles(organization: Organization) -> dict[str, Role]:
    return ensure_system_roles(organization)


@pytest.fixture
def make_user(organization: Organization, roles: dict[str, Role]):
    """Create a user holding a system role, optionally scoped to a branch."""

    def _make(
        email: str = "user@caesar.test",
        role: str = "CASHIER",
        *,
        password: str = "correct-horse-battery",  # noqa: S107 — test fixture
        pin: str | None = None,
        branch: Branch | None = None,
        org: Organization | None = None,
        full_name_ar: str = "مستخدم",
    ) -> User:
        target_org = org or organization
        user = User.objects.create_user(
            email=email,
            password=password,
            organization=target_org,
            full_name_ar=full_name_ar,
        )
        if pin:
            user.set_pin(pin)
            user.save(update_fields=["pin_hash", "pin_set_at"])

        role_obj = (
            roles[role] if target_org == organization else ensure_system_roles(target_org)[role]
        )
        RoleAssignment.objects.create(user=user, role=role_obj, branch=branch)
        return user

    return _make


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def authed(api: APIClient):
    """Return an APIClient authenticated as the given user."""

    def _auth(user: User, *, branch: Branch | None = None, kind: str = "WEB", device_id=None):
        from apps.accounts import tokens

        pair = tokens.issue_pair(
            user=user,
            kind=kind,
            organization_id=user.organization_id,
            branch_id=branch.id if branch else None,
            device_id=device_id,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
        client.token_pair = pair  # type: ignore[attr-defined]
        return client

    return _auth
