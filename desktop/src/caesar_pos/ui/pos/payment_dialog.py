"""
Taking payment.

The screen a cashier uses with a customer watching and a queue behind them, so:

  * **The amount is pre-filled with the balance due.** Paying in full is the
    common case and should take one tap.
  * **Change is computed as they type**, not after confirming. A cashier handed a
    100 needs the change figure before the drawer opens, not after.
  * **Quick-tender buttons** for the notes actually in circulation. "Customer
    gave me a 200" is two taps, not six.
  * **Split payment is not a special mode.** Enter less than the balance and the
    dialog closes having taken a partial payment; the order stays open with the
    remainder showing.

The dialog validates nothing that the service does not also validate. Its
refusals are for speed of feedback; the rules live in `orders.service`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: Egyptian notes in everyday circulation. A cashier reaches for what they were
#: handed, and these cover almost every cash transaction in a cafe.
QUICK_TENDERS = [Decimal("50"), Decimal("100"), Decimal("200"), Decimal("500")]

STYLESHEET = """
QLabel#DueLabel   { font-size: 16px; color: #475569; }
QLabel#DueValue   { font-size: 30px; font-weight: 800; color: #1d4e89; }
QLabel#ChangeGood { font-size: 22px; font-weight: 700; color: #15803d; }
QLabel#ChangeBad  { font-size: 16px; font-weight: 600; color: #b45309; }
QLineEdit#Amount  { font-size: 26px; padding: 14px; }
QPushButton#Method { background: #e2e8f0; color: #0f172a; }
QPushButton#MethodActive { background: #1d4e89; color: #ffffff; }
QPushButton#Tender { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; }
"""


class PaymentDialog(QDialog):
    """Emits `confirmed(method_id, amount, tendered)` and closes."""

    confirmed = Signal(str, object, object)

    def __init__(
        self,
        *,
        balance_due: Decimal,
        methods: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("الدفع")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(420)

        self.balance_due = balance_due
        self.methods = methods
        self.method_id: str | None = methods[0]["id"] if methods else None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        layout.addWidget(QLabel("المستحق", objectName="DueLabel"))
        due = QLabel(str(balance_due), objectName="DueValue")
        due.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(due)

        self._method_buttons: dict[str, QPushButton] = {}
        layout.addLayout(self._methods_row())

        # Pre-filled with the balance: paying in full is the common case and
        # should take one tap.
        self.amount = QLineEdit(str(balance_due), objectName="Amount")
        self.amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.amount.textChanged.connect(self._recompute)
        layout.addWidget(self.amount)

        layout.addLayout(self._tender_row())

        self.change = QLabel("", objectName="ChangeGood")
        self.change.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.change)

        buttons = QHBoxLayout()
        cancel = QPushButton("إلغاء", objectName="Secondary")
        cancel.clicked.connect(self.reject)
        self.confirm_button = QPushButton("تأكيد الدفع")
        self.confirm_button.clicked.connect(self._confirm)
        buttons.addWidget(cancel)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        self._tendered: Decimal | None = None
        self._recompute()

    # ── layout pieces ────────────────────────────────────────────────────────

    def _methods_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        for method in self.methods:
            button = QPushButton(method["name_ar"])
            button.setObjectName("MethodActive" if method["id"] == self.method_id else "Method")
            button.clicked.connect(lambda _=False, mid=method["id"]: self.select_method(mid))
            self._method_buttons[method["id"]] = button
            row.addWidget(button)

        return row

    def _tender_row(self):
        grid = QGridLayout()
        grid.setSpacing(8)

        # Only notes at or above the balance. Offering a 50 for a 68.40 bill is
        # a button that can only ever produce an error.
        offered = [note for note in QUICK_TENDERS if note >= self.balance_due][:3]
        for index, note in enumerate(offered):
            button = QPushButton(str(note), objectName="Tender")
            button.clicked.connect(lambda _=False, n=note: self.tender(n))
            grid.addWidget(button, 0, index)

        exact = QPushButton("بالظبط", objectName="Tender")
        exact.clicked.connect(lambda: self.tender(self.balance_due))
        grid.addWidget(exact, 0, len(offered))

        return grid

    # ── interaction ──────────────────────────────────────────────────────────

    def select_method(self, method_id: str) -> None:
        self.method_id = method_id
        for mid, button in self._method_buttons.items():
            button.setObjectName("MethodActive" if mid == method_id else "Method")
            button.style().unpolish(button)
            button.style().polish(button)

    def tender(self, note: Decimal) -> None:
        """
        The customer handed over this much. The AMOUNT stays the balance due;
        only the change moves — a 200 for a 68.40 bill is still a 68.40 sale.
        """
        self._tendered = note
        self.amount.setText(str(self.balance_due))
        self._recompute()

    @property
    def entered_amount(self) -> Decimal | None:
        try:
            return Decimal(self.amount.text().strip())
        except (InvalidOperation, ValueError):
            return None

    def _recompute(self) -> None:
        amount = self.entered_amount

        if amount is None or amount <= 0:
            self.change.setText("")
            self.confirm_button.setEnabled(False)
            return

        if amount > self.balance_due:
            self.change.setObjectName("ChangeBad")
            self.change.setText(f"المبلغ أكبر من المستحق ({self.balance_due})")
            self.confirm_button.setEnabled(False)
        elif self._tendered is not None and self._tendered >= amount:
            self.change.setObjectName("ChangeGood")
            self.change.setText(f"الباقي {self._tendered - amount}")
            self.confirm_button.setEnabled(True)
        else:
            self.change.setObjectName("ChangeGood")
            # Naming it a partial payment is what stops a cashier thinking the
            # bill is settled when it is not.
            remaining = self.balance_due - amount
            self.change.setText(f"دفعة جزئية — يتبقى {remaining}" if remaining > 0 else "")
            self.confirm_button.setEnabled(True)

        self.change.style().unpolish(self.change)
        self.change.style().polish(self.change)

    def _confirm(self) -> None:
        amount = self.entered_amount
        if amount is None or amount <= 0 or amount > self.balance_due or not self.method_id:
            return

        self.confirmed.emit(self.method_id, amount, self._tendered)
        self.accept()
