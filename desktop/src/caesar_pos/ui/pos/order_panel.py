"""
The current order: lines, totals, and the actions that change them.

Three things this panel refuses to do, each because of what it would cost:

  * **It never computes a total.** Every figure comes from the folded order,
    which came from `money.py`. A panel that added its own subtotal for display
    would eventually show a number the receipt disagrees with.
  * **It never hides a voided line's effect.** A void marks; the line stays
    visible, struck through, so the cashier can see what was removed and the
    customer can see it too.
  * **It shows the balance due, not just the total,** once anything is paid.
    Split payment is normal here, and "how much is left" is the number the
    cashier is actually working with.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...orders.events import ItemStatus
from ...orders.fold import FoldedOrder

STYLESHEET = """
QLabel#OrderNumber { font-size: 18px; font-weight: 700; }
QLabel#TotalLabel  { font-size: 16px; color: #475569; }
QLabel#TotalValue  { font-size: 16px; font-weight: 600; }
QLabel#GrandLabel  { font-size: 22px; font-weight: 700; }
QLabel#GrandValue  { font-size: 26px; font-weight: 800; color: #1d4e89; }
QLabel#DueValue    { font-size: 22px; font-weight: 800; color: #b45309; }
QLabel#EmptyOrder  { color: #64748b; font-size: 15px; }
QTableWidget { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; }
QTableWidget::item { padding: 8px; }
"""


class OrderPanel(QWidget):
    """Emits the intent; the window performs it and hands back a refolded order."""

    void_requested = Signal(str)  # line_id
    quantity_requested = Signal(str, object)  # line_id, Decimal
    note_requested = Signal(str)  # line_id
    discount_requested = Signal()
    fire_requested = Signal()
    pay_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.order: FoldedOrder | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.number = QLabel("—", objectName="OrderNumber")
        layout.addWidget(self.number)

        self.empty = QLabel("لا توجد أصناف بعد", objectName="EmptyOrder")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["الصنف", "الكمية", "الإجمالي", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.hide()
        layout.addWidget(self.table, stretch=1)

        layout.addLayout(self._totals_block())
        layout.addLayout(self._actions_block())

    def _totals_block(self):
        block = QVBoxLayout()
        block.setSpacing(6)

        self._rows: dict[str, tuple[QLabel, QLabel]] = {}
        for key, label in [
            ("subtotal", "المجموع"),
            ("discount", "الخصم"),
            ("service", "الخدمة"),
            ("tax", "الضريبة"),
        ]:
            row = QHBoxLayout()
            name = QLabel(label, objectName="TotalLabel")
            value = QLabel("0.00", objectName="TotalValue")
            value.setAlignment(Qt.AlignmentFlag.AlignLeft)
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(value)
            block.addLayout(row)
            self._rows[key] = (name, value)

        grand = QHBoxLayout()
        grand.addWidget(QLabel("الإجمالي", objectName="GrandLabel"))
        grand.addStretch(1)
        self.grand_value = QLabel("0.00", objectName="GrandValue")
        grand.addWidget(self.grand_value)
        block.addLayout(grand)

        self.due_row = QHBoxLayout()
        self.due_label = QLabel("المتبقي", objectName="GrandLabel")
        self.due_value = QLabel("0.00", objectName="DueValue")
        self.due_row.addWidget(self.due_label)
        self.due_row.addStretch(1)
        self.due_row.addWidget(self.due_value)
        block.addLayout(self.due_row)
        self._set_due_visible(False)

        return block

    def _actions_block(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        self.discount_button = QPushButton("خصم", objectName="Secondary")
        self.discount_button.clicked.connect(self.discount_requested.emit)

        self.fire_button = QPushButton("إرسال للمطبخ", objectName="Secondary")
        self.fire_button.clicked.connect(self.fire_requested.emit)

        self.pay_button = QPushButton("دفع")
        self.pay_button.clicked.connect(self.pay_requested.emit)

        for button in (self.discount_button, self.fire_button, self.pay_button):
            row.addWidget(button)

        return row

    # ── rendering ────────────────────────────────────────────────────────────

    def show_order(self, order: FoldedOrder | None) -> None:
        self.order = order

        if order is None:
            self.number.setText("—")
            self.table.hide()
            self.empty.show()
            self._render_totals(None)
            self._set_actions_enabled(False)
            return

        self.number.setText(order.local_number or "—")
        self._render_lines(order)
        self._render_totals(order)

        self.fire_button.setEnabled(bool(order.unfired_items))
        self.discount_button.setEnabled(bool(order.active_items))
        self.pay_button.setEnabled(bool(order.active_items) and not order.is_settled)

    def _render_lines(self, order: FoldedOrder) -> None:
        self.empty.setVisible(not order.items)
        self.table.setVisible(bool(order.items))
        self.table.setRowCount(len(order.items))

        for row, item in enumerate(order.items):
            voided = item.status == ItemStatus.VOIDED

            name = QTableWidgetItem(item.name_snapshot)
            if voided:
                # Struck through, not removed. The cashier can see what was
                # taken off, and so can the customer looking at the screen.
                font = name.font()
                font.setStrikeOut(True)
                name.setFont(font)

            quantity = QTableWidgetItem(_trim(item.quantity))
            total = QTableWidgetItem("—" if voided else str(item.line_total))
            for cell in (quantity, total):
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, quantity)
            self.table.setItem(row, 2, total)

            if not voided:
                remove = QPushButton("حذف", objectName="Secondary")
                remove.clicked.connect(
                    lambda _=False, line_id=item.line_id: self.void_requested.emit(line_id)
                )
                self.table.setCellWidget(row, 3, remove)
            else:
                self.table.setCellWidget(row, 3, QWidget())

    def _render_totals(self, order: FoldedOrder | None) -> None:
        if order is None or order.totals is None:
            for _, value in self._rows.values():
                value.setText("0.00")
            self.grand_value.setText("0.00")
            self._set_due_visible(False)
            return

        totals = order.totals
        # Read from the fold. Recomputing anything here would eventually show a
        # number the receipt disagrees with.
        self._rows["subtotal"][1].setText(str(totals.subtotal))
        self._rows["discount"][1].setText(str(totals.discount_total))
        self._rows["service"][1].setText(str(totals.service_total))
        self._rows["tax"][1].setText(str(totals.tax_total))
        self.grand_value.setText(str(totals.grand_total))

        # Only once something is paid — a "remaining" line that always says the
        # same as the total is noise the eye learns to skip.
        partly_paid = order.paid_total > Decimal("0") and not order.is_settled
        self._set_due_visible(partly_paid)
        if partly_paid:
            self.due_value.setText(str(order.balance_due))

    def _set_due_visible(self, visible: bool) -> None:
        self.due_label.setVisible(visible)
        self.due_value.setVisible(visible)

    def _set_actions_enabled(self, enabled: bool) -> None:
        for button in (self.discount_button, self.fire_button, self.pay_button):
            button.setEnabled(enabled)


def _trim(quantity: Decimal) -> str:
    """`2.000` reads as noise; `2` reads as a quantity."""
    text = f"{quantity:f}".rstrip("0").rstrip(".")
    return text or "0"
