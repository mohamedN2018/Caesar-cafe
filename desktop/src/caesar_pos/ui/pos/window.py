"""
The POS shell.

Grid on one side, order on the other, one permanent header. The header carries
three things and never hides any of them:

  * **who is on the till** — the screen is shared and the person changes hourly;
  * **the sync state** — a terminal queueing since Tuesday must say so;
  * **the way out** — logout is one tap, because the alternative is staff sharing
    a session all day and every void being attributed to whoever logged in first.

The window performs actions through `orders.service` and re-renders from what it
returns. It never mutates a `FoldedOrder` itself: the fold is the source, and a
panel that edited its own copy would drift from the events within one shift.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...local.db import Database
from ...orders import service
from ...security.session import Session
from . import catalog
from .order_panel import OrderPanel
from .payment_dialog import PaymentDialog
from .product_grid import ProductGrid

logger = logging.getLogger(__name__)

STYLESHEET = """
QWidget#Header { background: #ffffff; border-bottom: 1px solid #e2e8f0; }
QLabel#User { font-size: 15px; font-weight: 600; }
QLabel#Sync { font-size: 14px; color: #475569; }
QPushButton#Logout { background: #e2e8f0; color: #0f172a; padding: 8px 14px; }
QPushButton#NewOrder { padding: 10px 18px; }
"""


class PosWindow(QWidget):
    """Emits `logout_requested` — the app decides what happens next."""

    logout_requested = Signal()

    def __init__(
        self,
        db: Database,
        session: Session,
        settings: service.Settings,
        *,
        can_open_new_orders: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.session = session
        self.settings = settings
        # A RESTRICTED licence (C5): the terminal opens and open tables can still
        # be settled, but no new sale starts. Settling has to keep working — the
        # alternative is a cafe with money on its tables and no way to take it.
        self.can_open_new_orders = can_open_new_orders
        self.order_id: str | None = None

        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("كافيه القيصر — نقطة البيع")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(1100, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())

        body = QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(16)

        self.grid = ProductGrid()
        self.grid.chosen.connect(self._add)
        body.addWidget(self.grid, stretch=3)

        self.panel = OrderPanel()
        self.panel.void_requested.connect(self._void_line)
        self.panel.discount_requested.connect(self._discount)
        self.panel.fire_requested.connect(self._fire)
        self.panel.pay_requested.connect(self._pay)
        body.addWidget(self.panel, stretch=2)

        holder = QWidget()
        holder.setLayout(body)
        layout.addWidget(holder, stretch=1)

        self.grid.load(catalog.categories(db), catalog.tiles(db))
        self.panel.show_order(None)

    def _header(self) -> QWidget:
        header = QWidget(objectName="Header")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        self.new_order_button = QPushButton("طلب جديد", objectName="NewOrder")
        self.new_order_button.clicked.connect(self.new_order)
        row.addWidget(self.new_order_button)
        row.addStretch(1)

        self.sync_label = QLabel("", objectName="Sync")
        row.addWidget(self.sync_label)

        row.addWidget(QLabel(self.session.full_name_ar, objectName="User"))

        logout = QPushButton("خروج", objectName="Logout")
        logout.clicked.connect(self.logout_requested.emit)
        row.addWidget(logout)

        return header

    # ── the order ────────────────────────────────────────────────────────────

    def new_order(self) -> None:
        if not self.can_open_new_orders:
            self._refuse("الترخيص مقيّد — يمكن إنهاء الطلبات المفتوحة فقط. جدّد الاشتراك.")
            return

        order = service.open_order(self.db, settings=self.settings)
        self.order_id = order.order_id
        self.panel.show_order(order)

    def set_sync_label(self, text: str) -> None:
        self.sync_label.setText(text)

    def _add(self, tile) -> None:
        """
        A tap adds. An order is opened implicitly if there is not one — making a
        cashier press "new order" before the first item is a step that exists
        only because the software wanted it.
        """
        if self.order_id is None:
            self.new_order()
            if self.order_id is None:  # refused — a restricted licence
                return

        if tile.needs_variant_choice:
            tile = self._choose_variant(tile)
            if tile is None:
                return

        self._perform(lambda: service.add_item(self.db, self.order_id, variant_id=tile.variant_id))

    def _choose_variant(self, tile):
        options = catalog.variants_of(self.db, tile.product_id)
        labels = [f"{o.name_ar} — {o.price}" for o in options]

        choice, ok = QInputDialog.getItem(self, "اختر الحجم", tile.name_ar, labels, 0, False)
        if not ok:
            return None
        return options[labels.index(choice)]

    def _void_line(self, line_id: str) -> None:
        """
        Voiding after firing is a loss-prevention event, so it asks for a reason.
        Before firing it is routine and does not.
        """
        order = service.load(self.db, self.order_id)
        item = order.item(line_id)
        reason = ""

        if item is not None and item.fired_at is not None:
            reason, ok = QInputDialog.getText(self, "سبب الإلغاء", "الصنف أُرسل للمطبخ — السبب:")
            if not ok or not reason.strip():
                return

        self._perform(lambda: service.void_item(self.db, self.order_id, line_id, reason=reason))

    def _discount(self) -> None:
        if not self.session.can("orders.discount"):
            self._refuse("لا تملك صلاحية الخصم — اطلب موافقة مشرف.")
            return

        percent, ok = QInputDialog.getDouble(self, "خصم", "النسبة %", 0, 0, 100, 2)
        if not ok:
            return

        self._perform(
            lambda: service.apply_discount(self.db, self.order_id, percent=Decimal(str(percent)))
        )

    def _fire(self) -> None:
        self._perform(lambda: service.fire(self.db, self.order_id))

    def _pay(self) -> None:
        if not self.session.can("payments.take"):
            self._refuse("لا تملك صلاحية التحصيل.")
            return

        order = service.load(self.db, self.order_id)
        methods = catalog.payment_methods(self.db)
        if not methods:
            self._refuse("لا توجد طرق دفع — لم تتم المزامنة بعد.")
            return

        dialog = PaymentDialog(balance_due=order.balance_due, methods=methods, parent=self)
        dialog.confirmed.connect(self._take_payment)
        dialog.exec()

    def _take_payment(self, method_id: str, amount: Decimal, tendered) -> None:
        def run():
            return service.take_payment(
                self.db,
                self.order_id,
                method_id=method_id,
                amount=amount,
                tendered=tendered,
            )

        order = self._perform(run)

        if order is not None and order.is_settled:
            # The till clears itself. Leaving a paid order on screen is how the
            # next customer's coffee ends up on the last one's bill.
            self.order_id = None
            self.panel.show_order(None)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _perform(self, action):
        """
        Run a service call and re-render from what it returns.

        Every refusal the service raises is shown as it was written. The service
        messages are already in Arabic and already name the remedy; rewording
        them here would produce two vocabularies for the same rule.
        """
        try:
            order = action()
        except (service.OrderClosed, ValueError, LookupError) as exc:
            self._refuse(str(exc))
            return None

        self.panel.show_order(order)
        return order

    def _refuse(self, message: str) -> None:
        QMessageBox.warning(self, "تعذّر التنفيذ", message)

    def refresh_catalog(self) -> None:
        """Called after a catalog pull, so a new product appears without a restart."""
        self.grid.load(catalog.categories(self.db), catalog.tiles(self.db))
