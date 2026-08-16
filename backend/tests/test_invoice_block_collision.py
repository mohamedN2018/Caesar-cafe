"""
A device's first invoice block must not reuse numbers the branch already issued.

`_next_invoice_number` has two routes to a number:

  * a device consumes from its reserved `InvoiceBlock`
  * an order with no device takes `max(invoice_number) + 1` for the branch

`allocate_invoice_block` only ever consulted the first. So a branch that had sold
over the web — or been seeded, which is 56 invoices here — and then activated its
first till was handed a block starting at 1, and the first payment on that till
died on:

    UniqueViolation: duplicate key value violates unique constraint
    "uniq_invoice_serial"  DETAIL: Key (serial)=(MB-2026-000001) already exists.

Worth being precise about how bad that shape is. It fires at the moment of taking
money, on a till somebody has just set up, and it is **self-concealing**: the
IntegrityError rolls the transaction back, so the offending block disappears and
the table looks innocent to whoever goes looking.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.licensing import services
from apps.orders import services as order_services
from apps.payments.models import Invoice

pytestmark = pytest.mark.django_db


@pytest.fixture
def issued(organization, branch):
    return services.issue_license(
        organization=organization,
        branch=branch,
        license_type="YEARLY",
        max_devices=3,
        expires_at=timezone.now() + timedelta(days=365),
    )


@pytest.fixture
def device(issued):
    return services.activate(
        license_key=issued.plaintext_key,
        device_name="Cashier-01",
        branch=None,
        mode="POS",
        platform="web",
        app_version="test",
        fingerprint="",
        ip_address="127.0.0.1",
    ).device


def issue_deviceless(branch, number: int) -> Invoice:
    """
    An invoice numbered the way an order WITHOUT a device gets one.

    A real order behind it, because `Invoice.order` is not nullable — and because
    the allocator finds these by joining through `order__branch`, so an invoice
    with no order would not be found by the very query under test. The order is
    otherwise as bare as the model allows: what matters is that the NUMBER is
    taken, not how it was earned.
    """
    order = order_services.open_order(branch=branch, device_id=None)
    return Invoice.objects.create(
        order=order,
        invoice_number=number,
        serial=f"{branch.code}-{timezone.now():%Y}-{number:06d}",
        snapshot={},
    )


class TestTheFirstBlockOnABranchThatHasAlreadySold:
    def test_it_starts_after_the_numbers_already_issued(self, device, branch) -> None:
        """
        The regression itself. Before the fix this block began at 1, and the
        first payment on the new till collided with `MB-2026-000001`.
        """
        issue_deviceless(branch, 56)

        block = services.allocate_invoice_block(device)

        assert block.range_start == 57, (
            "a block starting at or below an already-issued number collides on "
            "uniq_invoice_serial at the first payment"
        )
        assert block.next_unused == 57

    def test_the_number_it_hands_out_is_free(self, device, branch) -> None:
        # The end that actually broke: a number is only useful if nothing has it.
        issue_deviceless(branch, 56)

        block = services.allocate_invoice_block(device)

        assert not Invoice.objects.filter(invoice_number=block.next_unused).exists()

    def test_an_untouched_branch_still_starts_at_one(self, device) -> None:
        # The fix must not push every new branch off 1. An accountant meeting a
        # first invoice numbered 57 has a different and worse question to ask.
        block = services.allocate_invoice_block(device)

        assert block.range_start == 1


class TestSubsequentBlocks:
    def test_blocks_remain_disjoint(self, device, branch) -> None:
        issue_deviceless(branch, 10)

        first = services.allocate_invoice_block(device, size=500)
        second = services.allocate_invoice_block(device, size=500)

        assert second.range_start == first.range_end + 1

    def test_a_reserved_range_still_wins_over_a_lower_invoice(self, device, branch) -> None:
        """
        Why the fix takes the MAX of both rather than switching sides.

        Blocks reserve ahead of use, so the highest block end routinely exceeds
        the highest issued invoice. Consulting only the invoice side would hand
        the next block numbers an existing block already owns — the same bug
        pointing the other way.
        """
        first = services.allocate_invoice_block(device, size=500)
        issue_deviceless(branch, 5)  # far inside the block that is already reserved

        second = services.allocate_invoice_block(device, size=500)

        assert second.range_start == first.range_end + 1
