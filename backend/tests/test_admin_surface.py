"""
What each role can actually reach in the Web Admin.

**Why this exists.** A cashier signed into the admin was being shown "أرصدة
المخزون", "حركة المخزون" and "الوصفات والتكلفة" — the last of which prints cost
and margin per product. None of that was a hole in the server: every screen was
gated, and gated on a permission the CASHIER role really does hold.

The mistake was which one. `catalog.view` exists so the till can draw a menu.
`inventory.view` accompanies `inventory.waste` so a cashier can look up the item
they are writing off — the role file says so in a comment. Both are *read*
permissions the point of sale needs, and both were being used to gate
*configuration and costing* screens in the admin, because they were the coarsest
permission that happened to include the right people.

So the rule this file enforces is not "the cashier cannot see X". It is:
**an admin screen is gated on a permission that matches what the screen does.**
A costing screen wants `catalog.manage_recipes`; a stock ledger wants
`reports.inventory`; station config wants `kitchen.manage_stations`. Those codes
all existed already and none of them belongs to an operational role.

Read across the monorepo, the same way `test_brand_parity.py` reads `brand.css`
and `test_floor_geometry.py` reads the Web's geometry: the permission lives in the
router, and a Python assertion about a TypeScript file is still cheaper than the
alternative, which is nobody checking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.authz.catalog import SYSTEM_ROLES

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
ROUTER = FRONTEND / "router" / "index.ts"
LAYOUT = FRONTEND / "layouts" / "AppLayout.vue"

#: `path: 'stock/movements',` … `meta: { permission: 'reports.inventory' },`
ROUTE = re.compile(
    r"path:\s*'([^']*)',\s*\n\s*name:\s*'[^']*',\s*\n\s*component:[^\n]*\n\s*"
    r"meta:\s*\{\s*permission:\s*'([^']+)'",
    re.MULTILINE,
)

#: `{ label: '…', to: '/stock', icon: '…', permission: 'reports.inventory' }`
NAV = re.compile(r"to:\s*'([^']+)'[^}]*?permission:\s*'([^']+)'")

#: Screens no operational role has any business opening. Configuration, costing,
#: ledgers, and anything that reads the whole branch's history.
ADMIN_ONLY = {
    "floor",
    "products",
    "categories",
    "recipes",
    "stock",
    "stock/movements",
    "kitchen/stations",
    "kids/tariffs",
    "kids/sessions",
    "suppliers",
    "purchasing",
    "staff",
    "hr/attendance",
    "hr/roster",
    "hr/timesheet",
    "licensing",
    "devices",
    "backups",
    "audit",
    "sync",
    "settings",
    "printers",
}

#: The roles somebody holds while standing behind a counter.
OPERATIONAL = ["CASHIER", "WAITER", "KITCHEN", "KIDS_STAFF"]


@pytest.fixture(scope="module")
def routes() -> dict[str, str]:
    assert ROUTER.exists(), f"the router has moved: {ROUTER}"
    found = dict(ROUTE.findall(ROUTER.read_text(encoding="utf-8")))
    # Guard the guard: a changed formatting convention would empty this dict and
    # make every assertion below pass while checking nothing.
    assert len(found) >= 20, f"only parsed {len(found)} gated routes — the regex has drifted"
    return found


def permissions_of(role: str) -> set[str]:
    return set(SYSTEM_ROLES[role]["permissions"])


class TestOperationalRolesCannotOpenAdminScreens:
    @pytest.mark.parametrize("role", OPERATIONAL)
    def test_role_unlocks_no_admin_only_screen(self, role: str, routes: dict[str, str]) -> None:
        held = permissions_of(role)
        reachable = sorted(
            f"{path} (gated on {permission})"
            for path, permission in routes.items()
            if path in ADMIN_ONLY and permission in held
        )

        assert not reachable, (
            f"{role} can open admin screens it has no use for. Gate each on the "
            "permission that matches what the screen DOES — the management codes "
            "already exist:\n  " + "\n  ".join(reachable)
        )

    def test_the_three_that_were_actually_reported(self, routes: dict[str, str]) -> None:
        """
        Named explicitly, because these are the ones somebody saw and had to
        report. `recipes` is the worst of them: it prints cost and margin per
        product, to the person taking the money.
        """
        cashier = permissions_of("CASHIER")

        for path in ("stock", "stock/movements", "recipes"):
            assert path in routes, f"{path} lost its permission gate entirely"
            assert routes[path] not in cashier, f"a cashier can still open {path}"


class TestTheManagerKeepsTheirJob:
    """
    Re-gating must not lock out the people the screens are for. Tightening a gate
    until nobody fits through is the other way to get this wrong, and it is the
    way that surfaces as a support call rather than as a complaint.
    """

    @pytest.mark.parametrize("path", sorted(ADMIN_ONLY - {"backups", "licensing"}))
    def test_a_branch_manager_can_still_open_it(self, path: str, routes: dict[str, str]) -> None:
        # backups and licensing are Super Admin only by design (docs/05).
        if path not in routes:
            pytest.skip(f"{path} declares no permission")
        assert routes[path] in permissions_of("BRANCH_MANAGER"), (
            f"a branch manager can no longer open {path} (now gated on {routes[path]})"
        )

    def test_a_super_admin_can_open_everything(self, routes: dict[str, str]) -> None:
        held = permissions_of("SUPER_ADMIN")
        assert not [p for p in routes.values() if p not in held]


class TestTheSidebarAgreesWithTheRouter:
    """
    A link the guard bounces is worse than no link.

    The sidebar and the router hold the same permission twice, in two files. When
    they disagree the user gets the one failure this product explicitly forbids:
    they are shown something, they click it, and they are refused for a request
    the interface made on their behalf.
    """

    def test_every_nav_entry_matches_its_route(self, routes: dict[str, str]) -> None:
        nav = dict(NAV.findall(LAYOUT.read_text(encoding="utf-8")))
        assert len(nav) >= 20, f"only parsed {len(nav)} nav entries — the regex has drifted"

        mismatched = sorted(
            f"{to}: sidebar says {permission}, router says {routes[to.lstrip('/')]}"
            for to, permission in nav.items()
            if to.lstrip("/") in routes and routes[to.lstrip("/")] != permission
        )

        assert not mismatched, "the sidebar and the router disagree:\n  " + "\n  ".join(mismatched)
