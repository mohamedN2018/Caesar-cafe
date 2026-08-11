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


def printed_key(output: str) -> str:
    """
    Pull the licence key out of the summary.

    Read from stdout rather than from the database on purpose: the plaintext is
    never stored, so what the operator can see is the only thing that can ever
    activate a terminal. A test that read the row would pass while the summary
    printed nothing.
    """
    for line in output.splitlines():
        if "licence key" in line:
            return line.split("licence key", 1)[1].strip()
    raise AssertionError(f"no licence key in the seed summary:\n{output}")


class TestTheDemoIsReachable:
    def test_the_seed_issues_a_licence_for_the_branch(self) -> None:
        seed()

        licence = License.objects.get()
        assert licence.branch is not None, "an org-wide licence cannot allocate invoice blocks"
        assert licence.status == LicenseStatus.PENDING
        assert licence.max_devices == 3

    def test_the_key_it_prints_actually_activates_a_terminal(self) -> None:
        """
        The whole point. Everything else about the licence can be right and the
        demo still unusable if the printed key does not open the till.
        """
        output = seed()
        licence = License.objects.get()

        activation = licensing_services.activate(
            license_key=printed_key(output),
            email=licence.customer_email,
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

    def test_the_summary_names_the_email_the_key_is_registered_to(self) -> None:
        """
        Activation compares the email in constant time and refuses a mismatch, so
        a key printed without the address it belongs to is a key that fails with
        a message about the wrong field.
        """
        output = seed()

        assert License.objects.get().customer_email in output

    def test_the_plaintext_key_is_never_stored(self) -> None:
        """
        Printed once, hashed at rest. If the row held the key, the summary would
        be a convenience rather than the only copy — and this test is what keeps
        the comment in `_license` honest.
        """
        output = seed()
        key = printed_key(output)
        licence = License.objects.get()

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
        licence = License.objects.get()
        licensing_services.activate(
            license_key=printed_key(output),
            email=licence.customer_email,
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

        assert License.objects.count() == 1, "exactly one licence, not two"
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

        assert License.objects.get().branch == Branch.objects.get(code="MB")
