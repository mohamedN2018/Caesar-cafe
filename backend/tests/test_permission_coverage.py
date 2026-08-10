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


def _declared_codes(view_class) -> list[str]:
    """
    Every permission code a view declares, flattened.

    A declaration may be a tuple meaning "any of these" (see
    `HasPermission`), and both passes below need the individual codes: one to
    check they exist in the catalogue, the other to count them as enforced.
    Treating a tuple as one opaque value would fail the first test with a
    confusing message and — worse — silently stop the second from counting the
    codes inside it, which is exactly the quiet gap that pass exists to find.
    """
    codes: list[str] = []
    if single := getattr(view_class, "required_permission", None):
        codes.append(single)

    per_method = getattr(view_class, "required_permissions", None)
    if isinstance(per_method, dict):
        for value in per_method.values():
            if not value:
                continue
            codes.extend([value] if isinstance(value, str) else list(value))
    return codes


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
            unknown.extend(
                f"{path}: {code}"
                for code in _declared_codes(view_class)
                if not catalog.is_valid(code)
            )

        assert not unknown, "Undefined permission codes:\n  " + "\n  ".join(unknown)


class TestEveryCatalogCodeIsActuallyEnforced:
    """
    The reverse direction, which is the one that fails quietly.

    `TestEveryRouteDeclaresAPermission` catches a route that forgot its code.
    Nothing caught a *code* that no route enforces — and that is not cosmetic:
    docs/05 tells an owner that a cashier cannot reprint a receipt, and if
    nothing checks `orders.reprint` then the matrix is describing a rule the
    product does not have.

    Found the hard way twice. Eleven `purchasing.*` codes sat unenforced because
    `apps/purchasing` had services and no views at all; four `staff.*` codes sat
    unenforced because there was no way to administer staff. Both looked complete
    from the role catalogue.

    A code counts as enforced if a route declares it, if some module checks it
    inline (`principal.has(...)`, `enforce_permission(...)`,
    `consume_approval_token(permission=...)`), or if it gates a settings key.
    Anything else must be listed below with the reason — which turns silent dead
    code into a decision somebody reviewed.
    """

    #: code -> why nothing enforces it yet. Every entry is a feature the product
    #: does not have, NOT an unguarded one. Delete the entry when it is built.
    NOT_YET_BUILT: dict[str, str] = {}

    def _declared_on_routes(self) -> set[str]:
        codes: set[str] = set()
        for _path, _name, view_class in _api_routes():
            codes.update(_declared_codes(view_class))
        return codes

    def _checked_inline(self) -> set[str]:
        """
        Codes named in a literal anywhere under `apps/`, outside the catalogue
        and the role definitions that merely list them.
        """
        import re
        from pathlib import Path

        apps_dir = Path(__file__).resolve().parents[1] / "apps"
        found: set[str] = set()

        for path in apps_dir.rglob("*.py"):
            if path.name == "catalog.py" and path.parent.name == "authz":
                continue
            if "migrations" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for code in catalog.PERMISSION_CODES:
                if re.search(rf"""['"]{re.escape(code)}['"]""", source):
                    found.add(code)
        return found

    def test_no_catalog_code_is_silently_unenforced(self) -> None:
        enforced = self._declared_on_routes() | self._checked_inline()
        orphans = sorted(set(catalog.PERMISSION_CODES) - enforced - set(self.NOT_YET_BUILT))

        assert not orphans, (
            "These permission codes exist in the catalogue and nothing enforces them. "
            "docs/05 promises a rule the product does not have. Either wire them up, "
            "or add them to NOT_YET_BUILT with the reason:\n  " + "\n  ".join(orphans)
        )

    def test_the_not_yet_built_list_stays_honest(self) -> None:
        """
        Every excuse must name a real code, and must still be an excuse — an
        entry left behind after the feature ships would hide the next regression
        in that area.
        """
        unknown = [code for code in self.NOT_YET_BUILT if not catalog.is_valid(code)]
        assert not unknown, f"NOT_YET_BUILT names codes that do not exist: {unknown}"

        enforced = self._declared_on_routes() | self._checked_inline()
        stale = sorted(set(self.NOT_YET_BUILT) & enforced)
        assert not stale, (
            "These are enforced now — remove them from NOT_YET_BUILT so the guard "
            f"protects them again: {stale}"
        )

    def test_the_guard_would_catch_a_regression(self) -> None:
        """A guard that cannot fail is not a guard."""
        enforced = self._declared_on_routes() | self._checked_inline()
        assert "orders.view" in enforced
        assert "purchasing.receive" in enforced, "the gap this guard was written for"
        assert "staff.reset_pin" in enforced, "and the second one"


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


class TestRolesAreCoherent:
    """
    You cannot mutate what you cannot see.

    A role holding a write capability without the matching read is incoherent:
    the UI hides the screen, the API refuses the lookup, and the user is asked
    to write off an item they are not allowed to find. Caught for real — CASHIER
    shipped with `inventory.waste` but no `inventory.view`.
    """

    #: write code -> the read code it cannot function without
    IMPLIES = {
        "inventory.waste": "inventory.view",
        "inventory.adjust": "inventory.view",
        "inventory.count": "inventory.view",
        "inventory.post_count": "inventory.view",
        "catalog.create": "catalog.view",
        "catalog.edit": "catalog.view",
        "catalog.change_price": "catalog.view",
        "catalog.manage_recipes": "catalog.view",
        "purchasing.create_po": "purchasing.view",
        "purchasing.receive": "purchasing.view",
        "purchasing.pay_supplier": "purchasing.view",
        "orders.create": "orders.view",
        "orders.edit_items": "orders.view",
        "orders.void_item": "orders.view",
        "orders.refund": "orders.view",
        "payments.take": "orders.view",
        "kids.checkin": "kids.view",
        "kids.checkout": "kids.view",
        "kitchen.update_status": "kitchen.view",
        "floor.open_table": "floor.view",
        "floor.transfer": "floor.view",
        "devices.manage": "devices.view",
        "licenses.manage": "licenses.view",
        "staff.manage_users": "staff.view",
    }

    def test_every_write_capability_has_its_read(self) -> None:
        problems = []
        for role, spec in catalog.SYSTEM_ROLES.items():
            held = set(spec["permissions"])
            problems.extend(
                f"{role}: has {write} but not {read}"
                for write, read in self.IMPLIES.items()
                if write in held and read not in held
            )
        assert not problems, "Incoherent roles:\n  " + "\n  ".join(problems)

    def test_the_guard_would_catch_a_regression(self) -> None:
        """A guard that cannot fail is not a guard."""
        broken = {"inventory.waste"}
        assert any(write in broken and read not in broken for write, read in self.IMPLIES.items())


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


class TestTheAccountantSeesEverythingAndChangesAlmostNothing:
    """
    "هيبقى في المحاسب بردو بيشوف كل حاجة زي الأدمن وبس."

    An accountant reconciling a month needs to open every screen an owner can,
    because "why is the 14th short" is not a question that stays inside the
    finance screens. Withholding them made the accountant ask an owner to read a
    screen aloud — and what actually happened next was the owner sharing a
    session, which is worse for control than the access would have been.

    So the role is every READ code in the catalogue, and the shortest possible
    list of writes. These tests are what keep the second half true: a permission
    added next year lands in the catalogue, and if somebody sweeps it into this
    role without thinking, the write test fails.
    """

    #: The only writes an accountant holds, and why each is the finance function
    #: itself rather than an operational power.
    ALLOWED_WRITES = {
        "purchasing.manage_suppliers",  # a supplier record is bookkeeping
        "purchasing.pay_supplier",  # paying one is the job
        "orders.reprint",  # a copy of a receipt changes nothing
    }

    @property
    def held(self) -> set[str]:
        return set(catalog.SYSTEM_ROLES["ACCOUNTANT"]["permissions"])

    def test_it_holds_every_read_code_in_the_catalogue(self) -> None:
        readable = {
            code
            for code in catalog.PERMISSION_CODES
            if code.endswith((".view", ".view_all")) or code.startswith("reports.")
        }
        missing = sorted(readable - self.held)

        assert not missing, (
            "the accountant is meant to see everything an owner can:\n  " + "\n  ".join(missing)
        )

    def test_it_holds_no_write_beyond_the_named_three(self) -> None:
        """
        The half that stops this becoming a second owner. Anything that is not
        a read and is not on the short list is a power an accountant does not
        need — and every one of them either moves money or changes a sale.
        """
        reads = {c for c in self.held if c.endswith((".view", ".view_all"))}
        reports = {c for c in self.held if c.startswith("reports.")}
        writes = self.held - reads - reports - self.ALLOWED_WRITES

        assert not writes, "an accountant must not be able to change these:\n  " + "\n  ".join(
            sorted(writes)
        )

    def test_it_cannot_touch_a_till_or_a_drawer(self) -> None:
        """Named individually, because these are the ones that cost money."""
        forbidden = {
            "orders.create",
            "orders.edit_items",
            "orders.void_item",
            "orders.void_order",
            "orders.discount",
            "orders.change_price",
            "orders.refund",
            "payments.take",
            "payments.split",
            "shifts.open",
            "shifts.close",
            "shifts.cash_movement",
            "inventory.adjust",
            "staff.manage_users",
            "staff.reset_pin",
        }
        assert not (self.held & forbidden)

    def test_every_code_it_names_actually_exists(self) -> None:
        """A typo here is a permission that silently does nothing."""
        unknown = sorted(c for c in self.held if not catalog.is_valid(c))
        assert not unknown, f"unknown codes on ACCOUNTANT: {unknown}"
