"""
The three drawer dialogs.

One of these tests matters more than the rest: the close screen must not show
the expected figure until a count has been entered. A cashier who can see
"should be 4,320" will find 4,320, and the variance report — the only mechanism
in the product for noticing a drawer is short — quietly stops working.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from caesar_pos.shifts import service as shifts
from caesar_pos.ui.shift import CashMovementDialog, CloseShiftDialog, OpenShiftDialog


def report(**overrides) -> shifts.ZReport:
    defaults = {
        "shift_id": "s1",
        "opening_cash": Decimal("500.00"),
        "cash_sales": Decimal("3820.00"),
        "non_cash_sales": Decimal("910.00"),
        "pay_ins": Decimal("0.00"),
        "pay_outs": Decimal("0.00"),
        "expected_cash": Decimal("4320.00"),
        "counted_cash": None,
        "order_count": 47,
    }
    return shifts.ZReport(**{**defaults, **overrides})


# ── opening ──────────────────────────────────────────────────────────────────


class TestOpenShiftDialog:
    def test_it_confirms_the_float(self, qtbot) -> None:
        dialog = OpenShiftDialog()
        qtbot.addWidget(dialog)
        dialog.amount.setText("500.00")

        confirmed = []
        dialog.confirmed.connect(confirmed.append)
        dialog._confirm()

        assert confirmed == [Decimal("500.00")]

    def test_zero_is_allowed(self, qtbot) -> None:
        """A drawer can genuinely start empty."""
        dialog = OpenShiftDialog()
        qtbot.addWidget(dialog)
        dialog.amount.setText("0")

        assert dialog.confirm_button.isEnabled()

    def test_nonsense_disables_the_button(self, qtbot) -> None:
        dialog = OpenShiftDialog()
        qtbot.addWidget(dialog)
        dialog.amount.setText("خمسمائة")

        assert not dialog.confirm_button.isEnabled()

    def test_a_negative_float_disables_the_button(self, qtbot) -> None:
        dialog = OpenShiftDialog()
        qtbot.addWidget(dialog)
        dialog.amount.setText("-5")

        assert not dialog.confirm_button.isEnabled()


# ── movements ────────────────────────────────────────────────────────────────


class TestCashMovementDialog:
    def test_it_will_not_confirm_without_a_reason(self, qtbot) -> None:
        dialog = CashMovementDialog()
        qtbot.addWidget(dialog)
        dialog.amount.setText("200")

        assert not dialog.confirm_button.isEnabled()

        dialog.reason.setText("لبن")
        assert dialog.confirm_button.isEnabled()

    def test_it_will_not_confirm_without_an_amount(self, qtbot) -> None:
        dialog = CashMovementDialog()
        qtbot.addWidget(dialog)
        dialog.reason.setText("لبن")

        assert not dialog.confirm_button.isEnabled()

    def test_it_emits_the_chosen_type(self, qtbot) -> None:
        dialog = CashMovementDialog()
        qtbot.addWidget(dialog)
        dialog._choose(shifts.PAY_IN)
        dialog.amount.setText("100")
        dialog.reason.setText("فكة")

        emitted = []
        dialog.confirmed.connect(lambda t, a, r: emitted.append((t, a, r)))
        dialog._confirm()

        assert emitted == [(shifts.PAY_IN, Decimal("100"), "فكة")]

    def test_a_payout_is_the_default(self, qtbot) -> None:
        """The common case: money leaving for a supplier at the door."""
        dialog = CashMovementDialog()
        qtbot.addWidget(dialog)

        assert dialog.movement_type == shifts.PAY_OUT


# ── closing ──────────────────────────────────────────────────────────────────


class TestCloseShiftDialog:
    def test_the_expected_figure_is_hidden_until_a_count_is_entered(self, qtbot) -> None:
        """
        The single most important interaction decision in the cash path. Showing
        it first turns counting into confirming.
        """
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)

        assert dialog.expected.isHidden()
        assert dialog.variance.isHidden()
        assert not dialog.confirm_button.isEnabled()

    def test_it_appears_once_the_cashier_has_counted(self, qtbot) -> None:
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4320.00")

        assert not dialog.expected.isHidden()
        assert "4320.00" in dialog.expected.text()

    def test_the_sales_figures_are_visible_from_the_start(self, qtbot) -> None:
        """
        Cash sales are not an answer key — the cashier cannot count them, and
        seeing them is what makes an obviously wrong total obvious.
        """
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)

        labels = [
            child.text()
            for child in dialog.findChildren(type(dialog.expected))
            if hasattr(child, "text")
        ]
        assert any("3820.00" in text for text in labels)
        assert not any("4320.00" in text for text in labels), "the expected total is not among them"

    def test_a_matching_count_says_so_in_words(self, qtbot) -> None:
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4320.00")

        assert "مطابق" in dialog.variance.text()
        assert dialog.confirm_button.isEnabled()

    def test_a_shortage_is_named_not_only_coloured(self, qtbot) -> None:
        """A red number on a washed-out screen is not a statement."""
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4275.00")

        assert "ناقص" in dialog.variance.text()
        assert "45.00" in dialog.variance.text()
        assert dialog.variance.objectName() == "VarianceShort"

    def test_an_overage_is_named_too(self, qtbot) -> None:
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4350.00")

        assert "زائد" in dialog.variance.text()
        assert dialog.variance.objectName() == "VarianceOver"

    def test_a_difference_must_be_explained_before_closing(self, qtbot) -> None:
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4275.00")

        assert not dialog.reason.isHidden()
        assert not dialog.confirm_button.isEnabled()

        dialog.reason.setText("نقص في الفكة")
        assert dialog.confirm_button.isEnabled()

    def test_a_matching_count_needs_no_explanation(self, qtbot) -> None:
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4320.00")

        assert dialog.reason.isHidden()
        assert dialog.confirm_button.isEnabled()

    def test_it_emits_the_count_and_the_reason(self, qtbot) -> None:
        dialog = CloseShiftDialog(report())
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.counted.setText("4275.00")
        dialog.reason.setText("نقص في الفكة")

        emitted = []
        dialog.confirmed.connect(lambda c, r: emitted.append((c, r)))
        dialog._confirm()

        assert emitted == [(Decimal("4275.00"), "نقص في الفكة")]

    def test_unpaid_tables_are_warned_about(self, qtbot) -> None:
        """
        Closing over an unpaid table attributes its bill to a drawer nobody is
        standing at. Said out loud rather than blocked — the cashier may be
        leaving it to the next shift on purpose.
        """
        dialog = CloseShiftDialog(report(), unsettled=2)
        qtbot.addWidget(dialog)

        labels = [
            child.text()
            for child in dialog.findChildren(type(dialog.expected))
            if hasattr(child, "text")
        ]
        assert any("2 طلب مفتوح" in text for text in labels)

    def test_no_warning_when_everything_is_settled(self, qtbot) -> None:
        dialog = CloseShiftDialog(report(), unsettled=0)
        qtbot.addWidget(dialog)

        labels = [
            child.text()
            for child in dialog.findChildren(type(dialog.expected))
            if hasattr(child, "text")
        ]
        assert not any("طلب مفتوح" in text for text in labels)


@pytest.mark.parametrize(
    ("counted", "expected_variance"),
    [("4320.00", Decimal("0.00")), ("4300.00", Decimal("-20.00")), ("4400.00", Decimal("80.00"))],
)
def test_the_dialog_and_the_service_agree_on_the_variance(
    qtbot, counted, expected_variance
) -> None:
    """
    Two implementations of "the difference" would drift, and the one on screen
    is the one the cashier signs off.
    """
    source = report()
    dialog = CloseShiftDialog(source)
    qtbot.addWidget(dialog)
    dialog.counted.setText(counted)

    from dataclasses import replace

    assert dialog.variance_value == expected_variance
    assert replace(source, counted_cash=Decimal(counted)).variance == expected_variance
