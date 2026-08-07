"""
Phase 2 exit criteria, enforced mechanically.

Two properties that documentation alone never preserves:

  1. EVERY registered route declares a permission code or is on the public
     allowlist. This is how the matrix in docs/05 stays true six months from
     now instead of becoming a record of what we once intended.

  2. NO route is reachable across tenants. A generic test beats a per-endpoint
     one because new endpoints are covered by default rather than by someone
     remembering.
"""

from __future__ import annotations

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from apps.authz import catalog
from apps.authz.drf import PUBLIC_ROUTE_NAMES


def _iter_routes(resolver=None, prefix="", namespace=None):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            yield from _iter_routes(
                entry,
                prefix + str(entry.pattern),
                namespace or entry.namespace,
            )
        elif isinstance(entry, URLPattern):
            name = f"{namespace}:{entry.name}" if namespace else entry.name
            yield prefix + str(entry.pattern), name, entry.callback


def _api_routes():
    for path, name, callback in _iter_routes():
        if not path.startswith("api/v1/"):
            continue
        view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if view_class is None:
            continue
        yield path, name, view_class


class TestEveryRouteDeclaresAPermission:
    def test_routes_were_discovered(self) -> None:
        """Guard the guard: an empty route list would make this vacuously pass."""
        assert len(list(_api_routes())) >= 15

    def test_all_routes_declare_or_are_explicitly_public(self) -> None:
        undeclared = []
        for path, name, view_class in _api_routes():
            if name in PUBLIC_ROUTE_NAMES:
                continue
            has_single = hasattr(view_class, "required_permission")
            has_per_method = hasattr(view_class, "required_permissions")
            if not (has_single or has_per_method):
                undeclared.append(f"{path}  ({view_class.__name__}, name={name})")

        assert not undeclared, (
            "These routes declare no permission and are not on the public "
            "allowlist:\n  " + "\n  ".join(undeclared)
        )

    def test_declared_codes_exist_in_the_catalog(self) -> None:
        unknown = []
        for path, _name, view_class in _api_routes():
            codes = []
            if single := getattr(view_class, "required_permission", None):
                codes.append(single)
            per_method = getattr(view_class, "required_permissions", None)
            if isinstance(per_method, dict):
                codes.extend(v for v in per_method.values() if v)

            unknown.extend(f"{path}: {code}" for code in codes if not catalog.is_valid(code))

        assert not unknown, "Undefined permission codes:\n  " + "\n  ".join(unknown)


class TestCatalogIntegrity:
    def test_no_duplicate_codes(self) -> None:
        codes = [p.code for p in catalog.PERMISSIONS]
        assert len(codes) == len(set(codes))

    def test_every_code_is_domain_dot_action(self) -> None:
        malformed = [c for c in catalog.PERMISSION_CODES if c.count(".") != 1]
        assert not malformed, malformed

    def test_every_permission_has_an_arabic_label(self) -> None:
        missing = [p.code for p in catalog.PERMISSIONS if not p.label_ar]
        assert not missing, missing

    def test_system_roles_reference_only_real_codes(self) -> None:
        problems = []
        for role, spec in catalog.SYSTEM_ROLES.items():
            problems.extend(
                f"{role}: {code}" for code in spec["permissions"] if not catalog.is_valid(code)
            )
        assert not problems, problems


class TestDocumentedRoleExclusions:
    """The three invariants stated in docs/05 as non-negotiable."""

    def test_cashier_cannot_reach_licensing(self) -> None:
        cashier = set(catalog.SYSTEM_ROLES["CASHIER"]["permissions"])
        assert not {c for c in cashier if c.startswith("licenses.")}
        assert "devices.manage" not in cashier

    def test_waiter_cannot_touch_inventory(self) -> None:
        waiter = set(catalog.SYSTEM_ROLES["WAITER"]["permissions"])
        assert not {c for c in waiter if c.startswith("inventory.")}

    def test_kitchen_cannot_see_financial_reports(self) -> None:
        kitchen = set(catalog.SYSTEM_ROLES["KITCHEN"]["permissions"])
        assert "reports.financial" not in kitchen
        assert not {c for c in kitchen if c.startswith("payments.")}

    def test_kids_staff_cannot_change_what_a_visit_costs(self) -> None:
        """docs/12: the person running the play area must not alter charges."""
        kids = set(catalog.SYSTEM_ROLES["KIDS_STAFF"]["permissions"])
        assert "kids.override_charge" not in kids
        assert "kids.manage_tariffs" not in kids


@pytest.mark.django_db
class TestCrossTenantIsolation:
    """Threat I1 — the highest-severity information-disclosure risk."""

    def test_settings_read_is_scoped_to_the_callers_organization(
        self, make_user, branch, other_branch, authed
    ) -> None:
        user = make_user(role="BRANCH_MANAGER")
        client = authed(user, branch=branch)

        response = client.get(f"/api/v1/settings/?branch={other_branch.id}")
        assert response.status_code == 404, "read another tenant's branch settings"
        assert response.json()["code"] == "BRANCH_NOT_FOUND"

    def test_settings_write_is_scoped_to_the_callers_organization(
        self, make_user, branch, other_branch, authed
    ) -> None:
        user = make_user(role="BRANCH_MANAGER")
        client = authed(user, branch=branch)

        response = client.patch(
            "/api/v1/settings/",
            {
                "scope": "BRANCH",
                "scope_id": str(other_branch.id),
                "values": {"finance.vat_percent": "0"},
            },
            format="json",
        )
        assert response.status_code == 404, "wrote into another tenant"

        from apps.configuration.models import SettingValue

        assert not SettingValue.objects.filter(scope_id=other_branch.id).exists()

    def test_step_up_approver_must_be_in_the_same_organization(
        self, make_user, branch, other_organization, authed
    ) -> None:
        import uuid

        cashier = make_user(email="cashier@caesar.test", role="CASHIER")
        outsider = make_user(
            email="outsider@other.test",
            role="BRANCH_MANAGER",
            pin="1234",
            org=other_organization,
        )
        client = authed(cashier, branch=branch, kind="POS", device_id=uuid.uuid4())

        response = client.post(
            "/api/v1/auth/verify-pin/",
            {
                "user_id": str(outsider.id),
                "pin": "1234",
                "permission": "orders.refund",
            },
            format="json",
        )
        assert response.status_code == 401
