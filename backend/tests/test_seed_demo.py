"""
The demo seed, and the one thing it forgot.

`seed_demo` builds a whole cafe — a fortnight of trading, a seated room, tickets
at every kitchen state, ten staff with a PIN each — and then locked the till. The
POS opens for nothing without a valid licence (C5), the seed issued none, and so
the first screen anybody saw after seeding was device activation with nothing to
type into it. Ten cashiers and no way for any of them to reach a keypad.

These tests are cheap on purpose: `--days 0`, because none of them are about the
trading. What they defend is that the demo is *reachable*, and that running it
twice does not leave a stale credential behind.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.licensing import services as licensing_services
from apps.licensing.models import Device, DeviceStatus, License, LicenseStatus
from apps.organizations.models import Branch

pytestmark = pytest.mark.django_db


def seed(*, days: int = 0, **kwargs) -> str:
    """
    Run the seed and hand back everything it printed.

    `days` is a real parameter rather than a hardcoded 0 with `**kwargs` on top:
    that version raised `TypeError: got multiple values for keyword argument`
    the moment a test wanted a trading day, which is a helper that only works
    for the cases written before it.
    """
    from io import StringIO

    out = StringIO()
    call_command("seed_demo", days=days, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


def printed_key(output: str, code: str = "MB") -> str:
    """
    Pull one branch's licence key out of the summary.

    `code` because a licence is issued per branch and the summary prints one line
    each. There is one branch today, so this reads like ceremony — it is not:
    matching on "licence key" alone would silently return whichever line came
    first, so the day a second branch is added the test would keep passing while
    that branch had no reachable key.

    Read from stdout rather than from the database on purpose: the plaintext is
    never stored, so what the operator can see is the only thing that can ever
    activate a terminal. A test that read the row would pass while the summary
    printed nothing.
    """
    for line in output.splitlines():
        if "licence key" in line and line.strip().startswith(code):
            return line.split("licence key", 1)[1].strip()
    raise AssertionError(f"no licence key for branch {code} in the seed summary:\n{output}")


class TestTheDemoIsReachable:
    def test_the_seed_issues_a_licence_for_the_branch(self) -> None:
        seed()

        licence = License.objects.get(branch__code="MB")
        assert licence.branch is not None, "an org-wide licence cannot allocate invoice blocks"
        assert licence.status == LicenseStatus.PENDING
        # Eight: two tills, the office, the manager, a kitchen screen, and room
        # to replace one without revoking another first. Seats stop one licence
        # running two cafés; they do not ration a café's own machines.
        assert licence.max_devices == 8

    def test_the_key_it_prints_actually_activates_a_terminal(self) -> None:
        """
        The whole point. Everything else about the licence can be right and the
        demo still unusable if the printed key does not open the till.
        """
        output = seed()

        activation = licensing_services.activate(
            license_key=printed_key(output),
            device_name="كاشير الباب",
            branch=None,
            mode="POS",
            platform="web",
            app_version="test",
            fingerprint="",
            ip_address="127.0.0.1",
        )

        assert activation.device.status == DeviceStatus.ACTIVE
        assert activation.device_secret, "a terminal with no secret cannot authenticate"

    def test_the_summary_asks_for_nothing_but_the_key(self) -> None:
        """
        Activation needs the key and a device name. It used to need a registered
        email too, and the summary printed that address beside the key because a
        key without it failed with a message about the wrong field.

        Both are gone. This asserts the summary does not resurrect the address —
        printing a credential nobody is asked for is how a stale instruction
        survives a simplification.
        """
        output = seed()

        assert "licence email" not in output
        assert "licence key" in output

    def test_the_plaintext_key_is_never_stored(self) -> None:
        """
        Printed once, hashed at rest. If the row held the key, the summary would
        be a convenience rather than the only copy — and this test is what keeps
        the comment in `_license` honest.
        """
        output = seed()
        key = printed_key(output)
        licence = License.objects.get(branch__code="MB")

        assert key not in licence.key_hash
        assert licence.key_hash != key
        # The prefix and last group ARE stored, deliberately — an admin has to be
        # able to tell two licences apart in a list.
        assert licence.key_prefix in key


class TestReseeding:
    def test_a_reset_leaves_no_licence_or_device_behind(self) -> None:
        """
        A device secret outliving its licence is a stale credential that keeps
        answering — a re-seeded demo where an old till can still sell. The reset
        deletes in dependency order because `InvoiceBlock.device` is PROTECT, so
        a cascade would stop half-way and leave the database neither seeded nor
        reset.
        """
        output = seed()
        licensing_services.activate(
            license_key=printed_key(output),
            device_name="كاشير الباب",
            branch=None,
            mode="POS",
            platform="web",
            app_version="test",
            fingerprint="",
            ip_address="127.0.0.1",
        )
        assert Device.objects.count() == 1

        seed(reset=True)

        # One per branch, not two per branch. The point is unchanged — the reset
        # must not leave the previous run's licence beside the new one — but the
        # expected count is now the branch count rather than 1.
        assert License.objects.count() == Branch.objects.count(), (
            "the reset left a licence from the previous run behind"
        )
        assert Device.objects.count() == 0, "the old terminal's credential survived the reset"

    def test_the_second_run_prints_a_different_key(self) -> None:
        """
        Not a fixed constant, even though the rest of this command is
        deterministic. `keys.generate` uses `secrets` because `random` is a
        Mersenne Twister and a few outputs predict the rest; hardcoding a demo
        key would make the seed the counter-example to its own module's argument.
        A reset deletes the devices anyway, so a re-activation was already
        required and a stable key would have saved copying a string, not a step.
        """
        first = printed_key(seed())
        second = printed_key(seed(reset=True))

        assert first != second

    def test_seeding_twice_without_reset_is_still_refused(self) -> None:
        """
        The guard that stops demo trading being mixed into a real ledger has to
        survive this change — `--days 0` writes no orders, so this asserts the
        licence work did not accidentally become the thing that runs first and
        leaves a second licence behind on a refused run.
        """
        seed(days=1)
        before = License.objects.count()

        with pytest.raises(Exception, match="already holds"):
            call_command("seed_demo", days=1)

        assert License.objects.count() == before


class TestTheBranchIsWiredUp:
    def test_the_licence_belongs_to_the_branch_the_demo_trades_in(self) -> None:
        """
        Invoice numbers come from a block reserved per branch (C9). A licence
        pointing at a different branch than the one the tills sell in would
        allocate against the wrong counter.
        """
        seed()

        assert License.objects.get(branch__code="MB").branch == Branch.objects.get(code="MB")


class TestEveryVariantHasACost:
    """
    The demo reported a 94% gross margin, and that was the data, not the report.

    Only 13 of 56 variants have a recipe, and a variant's cost is computed from its
    recipe when stock is received. The other 43 kept `cost = 0`; the order service
    snapshotted that faithfully onto every line it sold; and a fortnight of trading
    came out as 712,437 in sales against 41,067 of cost.

    Every report built on cost was wrong at once — the P&L, product profitability,
    the margin column, the dashboard — and none of them were broken. **A zero cost
    is not a missing value, it is an assertion of infinite margin**, and it reads
    like an empty field, which is why it survived this long. `_reset` already
    carried a comment about the same symptom from a different cause.
    """

    def test_no_variant_is_left_costing_nothing(self) -> None:
        from decimal import Decimal

        from apps.catalog.models import ProductVariant

        seed()

        free = ProductVariant.objects.filter(cost__lte=Decimal("0")).values_list(
            "name_ar", flat=True
        )
        assert not list(free), "these variants would be sold at 100% margin: " + ", ".join(free)

    def test_a_recipe_costed_variant_keeps_its_computed_cost(self) -> None:
        """
        The backfill must not flatten the eleven that were costed properly.

        `_receive_stock` recosts the recipe-backed variants with an UPDATE, so the
        in-memory objects the seed is holding still read zero. The first version of
        this fix used those, which would have overwritten a cost derived from a real
        weighted average with a flat percentage of the price — and it would have
        looked correct, because every variant ends up with a plausible number
        either way. Only the ratio gives it away.
        """
        from decimal import Decimal

        from apps.catalog.models import ProductVariant

        seed()

        cappuccino = ProductVariant.objects.get(
            product__sku="CAPP", name_ar="وسط", product__branch__code="MB"
        )

        # Costed from beans, milk and a cup — nowhere near the 22% of price that a
        # drink with no recipe would have been given.
        ratio = cappuccino.cost / cappuccino.price
        assert ratio != Decimal("0.22"), (
            f"cappuccino cost {cappuccino.cost} is exactly 22% of its price, "
            "which means the recipe cost was overwritten by the fallback"
        )
        assert cappuccino.cost > Decimal("0")

    def test_food_costs_more_of_its_price_than_a_drink_does(self) -> None:
        """
        Plausibility, not just non-zero.

        A café makes its money on drinks: a latte is a spoon of beans and some milk,
        a burger is mostly beef. If the fallback ever collapses to one flat ratio the
        numbers stay non-zero and stop meaning anything, and this is the cheapest way
        to notice.
        """
        from apps.catalog.models import ProductVariant

        seed()

        tea = ProductVariant.objects.get(product__sku="TEA", product__branch__code="MB")
        burger = ProductVariant.objects.get(
            product__sku="BURGER", name_ar="سنجل", product__branch__code="MB"
        )

        assert burger.cost / burger.price > tea.cost / tea.price


class TestEveryBranchIsSeeded:
    """
    Three branches, and each one has to stand on its own.

    One branch could never show a query that forgot to filter by branch — the right
    answer and the wrong answer are the same number — so the seed builds three. The
    risk that introduces is the opposite one: a second branch that exists in the
    branch list and nowhere else, so a name is counted everywhere and every screen
    behind it is empty.
    """

    def test_every_branch_has_its_own_licence(self) -> None:
        # A licence is per branch, and a till activated against another branch's key
        # is refused. Two branches sharing one would be two branches that cannot
        # open a till.
        seed()

        for branch in Branch.objects.all():
            assert License.objects.filter(branch=branch).exists(), (
                f"branch {branch.code} has no licence, so no till there can be activated"
            )

    def test_the_summary_prints_a_key_for_each_branch(self) -> None:
        """
        Printed once and never stored. A branch whose key does not reach stdout is a
        branch nobody can activate, and the only remedy is regenerating it.
        """
        output = seed()

        keys = {printed_key(output, branch.code) for branch in Branch.objects.all()}

        assert len(keys) == Branch.objects.count(), "two branches were given the same key"

    def test_every_branch_has_its_own_catalogue_and_stock(self) -> None:
        from apps.catalog.models import ProductVariant
        from apps.inventory.models import InventoryItem

        seed()

        for branch in Branch.objects.all():
            assert ProductVariant.objects.filter(product__branch=branch).exists(), (
                f"branch {branch.code} has an empty menu"
            )
            assert InventoryItem.objects.filter(branch=branch).exists(), (
                f"branch {branch.code} has no stock"
            )

    def test_a_cashier_can_work_at_every_branch(self) -> None:
        """
        Otherwise the demo shows three branches of data and lets you log into one.

        That reads as a permissions bug in the product rather than a gap in the seed,
        which is the expensive kind of wrong: somebody goes looking in `authz`.
        """
        from apps.accounts.models import User
        from apps.authz.models import RoleAssignment

        seed()

        cashier = User.objects.get(email="cashier@caesar.test")
        assigned = set(
            RoleAssignment.objects.filter(user=cashier).values_list("branch__code", flat=True)
        )

        assert assigned >= {b.code for b in Branch.objects.all()}


class TestShrinkingTheBranchList:
    """
    A branch the seed stopped defining must not be left looking open.

    The reset empties each branch's trading and had no reason to touch the `Branch`
    rows, so going from three branches back to one left two behind with everything
    underneath them deleted. An active branch with nothing in it still scopes
    queries and still counts wherever the code iterates branches, and every figure
    it produces is zero — which reads as a quiet week, not as a branch that should
    not exist.

    Retired rather than removed, because that is what the schema says. `Branch` is a
    `SoftDeletableModel` and seven models PROTECT it; a hard delete collected ninety
    protected rows on the first attempt. A demo command does not get to overrule a
    constraint the product states on purpose.
    """

    @staticmethod
    def _stale_branch() -> Branch:
        from apps.organizations.models import Organization

        org = Organization.objects.get(name_en="Caesar Cafe")
        return Branch.objects.create(
            organization=org, code="ZM", name_ar="فرع قديم", name_en="Old Branch"
        )

    def test_a_branch_no_longer_defined_is_retired_by_the_reset(self) -> None:
        seed()
        stale = self._stale_branch()

        seed(reset=True)

        stale.refresh_from_db()
        assert stale.is_active is False
        assert stale.deactivated_at is not None

    def test_the_branch_still_in_the_list_is_untouched(self) -> None:
        # The obvious way to get this wrong is a filter that retires everything.
        seed()
        self._stale_branch()

        seed(reset=True)

        assert Branch.objects.get(code="MB").is_active is True

    def test_the_reset_says_which_branches_it_retired(self) -> None:
        # Retiring a branch is not quiet housekeeping — it should be visible in the
        # output that it happened, and to which branch.
        seed()
        self._stale_branch()

        output = seed(reset=True)

        assert "ZM" in output
        assert "no longer defines" in output

    def test_another_organisation_s_branches_are_untouched(self) -> None:
        """
        The narrowest scope that does the job.

        A reset that retired every branch whose code is not in this file would be a
        demo command reaching into a real café's data.
        """
        from apps.organizations.models import Organization

        other = Organization.objects.create(name_ar="مطعم آخر", name_en="Another Place")
        theirs = Branch.objects.create(
            organization=other, code="ZM", name_ar="فرعهم", name_en="Their Branch"
        )

        seed()
        seed(reset=True)

        theirs.refresh_from_db()
        assert theirs.is_active is True, "the demo reset retired another organisation's branch"
