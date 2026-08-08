"""
Receipts and printing.

The receipt is the one artefact of this system a customer takes home, and in
Egypt it is also a tax document. Two properties matter more than anything else
here:

  * the figures on it are the fold's, never recomputed;
  * a printer failure never blocks a sale.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service
from caesar_pos.printing import arabic, receipt, spooler


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "print.db")
    yield Database(connection)
    connection.close()


@pytest.fixture
def menu(db):
    db.upsert_mirror(
        "m_products",
        {"id": "p1", "name_ar": "كابتشينو", "payload": json.dumps({"is_tax_exempt": False})},
    )
    db.upsert_mirror(
        "m_variants",
        {"id": "v1", "product_id": "p1", "price": "60.00", "cost": "18.00", "payload": "{}"},
    )


@pytest.fixture
def settings():
    return service.Settings(
        vat_percent=Decimal("14.00"),
        vat_enabled=True,
        vat_inclusive=False,
        service_percent=Decimal("0.00"),
        service_enabled=False,
        rounding_step=Decimal("0.01"),
    )


@pytest.fixture
def order(db, menu, settings):
    made = service.open_order(db, settings=settings)
    return service.add_item(db, made.order_id, variant_id="v1", quantity=Decimal("2"))


# ── Arabic shaping ───────────────────────────────────────────────────────────


class TestArabicShaping:
    def test_arabic_is_detected(self) -> None:
        assert arabic.has_arabic("كابتشينو") is True
        assert arabic.has_arabic("Cappuccino") is False
        assert arabic.has_arabic("2× كابتشينو") is True

    def test_shaping_changes_the_string(self) -> None:
        """
        Reshaping picks the initial/medial/final form of every letter, and the
        bidi pass reorders the run. Sending the raw string to a printer with no
        shaping engine produces disconnected letters in the wrong order.
        """
        raw = "كابتشينو"
        shaped = arabic.shape(raw)

        assert shaped != raw
        assert len(shaped) >= len(raw) - 2

    def test_latin_passes_through_unchanged(self) -> None:
        assert arabic.shape("MB-01-0042") == "MB-01-0042"

    def test_it_renders_to_a_1bit_image(self) -> None:
        """
        Mode "1" is one bit per pixel — exactly what the printer wants, and small
        enough to send over a slow serial link.
        """
        image = arabic.render(["كافيه القيصر", "MB-01-0042"])

        assert image.mode == "1"
        assert image.width == arabic.PAPER_WIDTH_PX
        assert image.height > 0

    def test_the_narrow_roll_is_supported(self) -> None:
        image = arabic.render(["كافيه القيصر"], arabic.RenderOptions(width=arabic.NARROW_WIDTH_PX))
        assert image.width == arabic.NARROW_WIDTH_PX

    def test_more_lines_make_a_taller_image(self) -> None:
        short = arabic.render(["سطر"])
        tall = arabic.render(["سطر"] * 10)

        assert tall.height > short.height


# ── the receipt document ─────────────────────────────────────────────────────


class TestReceipt:
    def test_the_total_is_the_folds(self, order) -> None:
        """
        A receipt that added its own total would be the one place the customer's
        copy and the server's record disagree.
        """
        document = receipt.build(order)
        text = document.as_text()

        assert str(order.totals.grand_total) in text
        assert document.meta["grand_total"] == "136.80"

    def test_it_names_the_item_as_sold(self, order) -> None:
        assert "كابتشينو" in receipt.build(order).as_text()

    def test_zero_rows_are_omitted(self, order) -> None:
        """
        A receipt listing "الخصم 0.00" on every sale trains the eye to skip the
        block where a real discount would appear.
        """
        text = receipt.build(order).as_text()

        assert "الخصم" not in text
        assert "الخدمة" not in text
        assert "ضريبة القيمة المضافة" in text, "but a real VAT line IS shown"

    def test_a_discount_appears_when_there_is_one(self, db, order) -> None:
        """
        The discount comes off the net, before VAT — so 10% of a 120.00 subtotal
        is 12.00, not 10% of the VAT-inclusive 136.80. The receipt prints what
        the fold decided; this asserts it did not quietly print the other number.
        """
        discounted = service.apply_discount(db, order.order_id, percent=Decimal("10"))
        text = receipt.build(discounted).as_text()

        assert "الخصم" in text
        assert "-12.00" in text
        assert str(discounted.totals.grand_total) in text

    def test_a_provisional_serial_says_what_it_is(self, order) -> None:
        """
        A customer holding a slip whose number differs from the emailed copy
        needs to be able to connect them (C9).
        """
        text = receipt.build(order, serial="MB-01-P042", provisional=True).as_text()

        assert "MB-01-P042" in text
        assert "مؤقتة" in text

    def test_an_ordinary_receipt_says_nothing_about_provisional(self, order) -> None:
        assert "مؤقتة" not in receipt.build(order, serial="MB-2026-000123").as_text()

    def test_a_partial_payment_shows_the_remainder(self, db, order) -> None:
        paid = service.take_payment(
            db, order.order_id, method_id="m-cash", amount=Decimal("100.00")
        )
        text = receipt.build(paid).as_text()

        assert "المتبقي" in text
        assert "36.80" in text

    def test_a_voided_line_is_not_on_the_receipt(self, db, order) -> None:
        """
        The void is on the audit trail and the order screen. A customer's copy
        listing something they were not charged for is a conversation nobody
        wants to have.
        """
        voided = service.void_item(db, order.order_id, order.items[0].line_id, reason="غلط")
        assert "كابتشينو" not in receipt.build(voided).as_text()

    def test_the_header_details_are_included(self, order) -> None:
        header = receipt.ReceiptHeader(
            branch_name="كافيه القيصر", tax_number="123-456-789", phone="0100"
        )
        text = receipt.build(order, header=header).as_text()

        assert "123-456-789" in text
        assert "0100" in text


class TestKitchenTicket:
    def test_it_holds_no_prices(self, order) -> None:
        """
        The kitchen needs to know what to make. A price on this slip is
        information leaking to a part of the operation that has no use for it.
        """
        text = receipt.build_kitchen_ticket(order, station_name="بار القهوة").as_text()

        assert "كابتشينو" in text
        assert "60.00" not in text
        assert "136.80" not in text

    def test_the_quantity_comes_first(self, order) -> None:
        """A cook reads the number across a noisy kitchen; a 2 read as a 1 is a remake."""
        lines = receipt.build_kitchen_ticket(order).lines
        item_line = next(line for line in lines if "كابتشينو" in line)

        assert item_line.strip().startswith("2×")

    def test_it_names_the_station(self, order) -> None:
        document = receipt.build_kitchen_ticket(order, station_name="الحلويات")
        assert document.lines[0] == "الحلويات"
        assert document.meta["station"] == "الحلويات"

    def test_only_unfired_items_are_on_it(self, db, order) -> None:
        fired = service.fire(db, order.order_id)
        document = receipt.build_kitchen_ticket(fired)

        assert document.meta["items"] == 0, "a second slip must not repeat the first round"


# ── the queue ────────────────────────────────────────────────────────────────


class FakePrinter:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.printed: list[list[str]] = []
        self.fail_times = fail_times

    def print_document(self, lines, printer_name=""):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("printer is out of paper")
        self.printed.append(lines)


class TestSpooler:
    def test_a_document_is_queued_and_printed(self, db, order) -> None:
        spooler.enqueue(db, receipt.build(order))
        printer = FakePrinter()

        assert spooler.drain(db, printer) == {"printed": 1, "failed": 0}
        assert printer.printed
        assert spooler.counts(db)["pending"] == 0

    def test_a_printer_failure_never_blocks_the_sale(self, db, order) -> None:
        """
        Printing inside the payment transaction means a paper jam stops the till.
        That is worse than a customer waiting ten seconds, every single time.
        """
        spooler.enqueue(db, receipt.build(order))
        result = spooler.drain(db, FakePrinter(fail_times=1))

        assert result == {"printed": 0, "failed": 1}
        assert spooler.counts(db)["pending"] == 1, "still queued, not lost"

    def test_it_drains_when_the_printer_comes_back(self, db, order) -> None:
        spooler.enqueue(db, receipt.build(order))
        spooler.drain(db, FakePrinter(fail_times=1))

        printer = FakePrinter()
        assert spooler.drain(db, printer)["printed"] == 1

    def test_it_gives_up_after_the_cap(self, db, order) -> None:
        """
        A printer that has refused six times is out of paper or switched off.
        Retrying every two seconds for an hour only fills the log.
        """
        spooler.enqueue(db, receipt.build(order))
        printer = FakePrinter(fail_times=spooler.MAX_ATTEMPTS + 2)

        for _ in range(spooler.MAX_ATTEMPTS + 2):
            spooler.drain(db, printer)

        counts = spooler.counts(db)
        assert counts["pending"] == 0
        assert counts["failed"] == 1

    def test_failed_jobs_can_be_retried_by_a_human(self, db, order) -> None:
        spooler.enqueue(db, receipt.build(order))
        printer = FakePrinter(fail_times=spooler.MAX_ATTEMPTS)
        for _ in range(spooler.MAX_ATTEMPTS):
            spooler.drain(db, printer)

        assert spooler.retry_failed(db) == 1
        assert spooler.drain(db, FakePrinter())["printed"] == 1

    def test_the_queue_survives_a_restart(self, db, order, tmp_path) -> None:
        """Rows in SQLite, not objects in memory. A crash loses no receipts."""
        spooler.enqueue(db, receipt.build(order))
        db.connection.close()

        reopened = Database(connect(tmp_path / "print.db"))
        assert len(spooler.pending(reopened)) == 1

    def test_a_kitchen_ticket_and_a_receipt_queue_separately(self, db, order) -> None:
        spooler.enqueue(db, receipt.build(order))
        spooler.enqueue(db, receipt.build_kitchen_ticket(order), printer="kitchen")

        jobs = spooler.pending(db)
        assert {job.kind for job in jobs} == {"RECEIPT", "KITCHEN"}
        assert {job.printer for job in jobs} == {"", "kitchen"}
