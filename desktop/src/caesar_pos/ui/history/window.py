"""
Today's orders, and reprinting one.

The question this answers is asked several times a shift: *"the receipt for the
table that just left — can I have it again?"* Until now the only order a
terminal could show was the one on screen, so the answer was no.

Two rules it inherits rather than invents:

  * **A reprint is a duplicate, and duplicates are tracked.** A second copy of a
    paid receipt is the paperwork a refund fraud needs, so it needs
    `orders.reprint` and it is queued as a marked reprint, not as a fresh sale.
  * **The figures come from the fold, never recomputed.** The receipt builder is
    the same one payment uses, so a reprint six months from now is the document
    the customer took home.

Scoped to what this terminal knows. During an outage that is only this device's
sales, and the screen says so rather than implying it holds the whole day.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...local.db import Database
from ...orders import service
from ...printing import receipt as receipts
from ...printing import spooler
from ...security.session import Session
from .. import palette as p

logger = logging.getLogger(__name__)

STYLESHEET = f"""
QLabel#Title   {{ font-size: 20px; font-weight: 700; }}
QLabel#Hint    {{ font-size: 13px; color: {p.INK_MUTED}; }}
QLabel#Empty   {{ font-size: 15px; color: {p.INK_MUTED}; }}
QLabel#Number  {{ font-size: 16px; font-weight: 700; }}
QLabel#Meta    {{ font-size: 12px; color: {p.INK_MUTED}; }}
QLabel#Amount  {{ font-size: 17px; font-weight: 800; color: {p.BRAND_700}; }}
QFrame#Row     {{ background: {p.SURFACE}; border: 1px solid {p.BORDER};
                  border-radius: 10px; }}
QPushButton#Reprint {{ background: {p.SURFACE_SUNKEN}; color: {p.INK}; padding: 8px 14px; }}
QPushButton#Open    {{ background: {p.BRAND_700}; color: {p.FG_ON_BRAND}; padding: 8px 14px; }}
"""

STATUS_LABELS = {
    "OPEN": "مفتوح",
    "IN_KITCHEN": "في المطبخ",
    "READY": "جاهز",
    "SERVED": "تم التقديم",
    "PAID": "مدفوع",
    "VOIDED": "ملغى",
}


class HistoryDialog(QDialog):
    """Emits `reopen_requested(order_id)` for an order still open."""

    reopen_requested = Signal(str)

    def __init__(
        self,
        db: Database,
        session: Session,
        *,
        header: receipts.ReceiptHeader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.session = session
        self.header = header or receipts.ReceiptHeader()

        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("طلبات اليوم")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        layout.addWidget(QLabel("طلبات اليوم", objectName="Title"))
        layout.addWidget(
            QLabel(
                "طلبات هذا الجهاز. أثناء انقطاع الشبكة لن تظهر طلبات الأجهزة الأخرى.",
                objectName="Hint",
            )
        )

        self.search = QLineEdit()
        self.search.setPlaceholderText("ابحث برقم الطلب…")
        self.search.textChanged.connect(lambda _: self.refresh())
        layout.addWidget(self.search)

        self.empty = QLabel("لا توجد طلبات اليوم.", objectName="Empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        self.list = QVBoxLayout()
        self.list.setSpacing(6)
        holder = QWidget()
        holder.setLayout(self.list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

        self.message = QLabel("", objectName="Hint")
        layout.addWidget(self.message)

        close = QPushButton("إغلاق")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignLeft)

        self.refresh()

    @property
    def orders(self) -> list[dict]:
        return service.recent_orders(self.db, search=self.search.text().strip())

    def refresh(self) -> None:
        _clear(self.list)
        rows = self.orders
        self.empty.setVisible(not rows)

        for order in rows:
            self.list.addWidget(self._row(order))
        self.list.addStretch(1)

    def _row(self, order: dict) -> QFrame:
        frame = QFrame(objectName="Row")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        details = QVBoxLayout()
        details.setSpacing(2)
        details.addWidget(QLabel(order["local_number"], objectName="Number"))
        details.addWidget(
            QLabel(
                f"{STATUS_LABELS.get(order['status'], order['status'])}"
                f" · {order['opened_at'][11:16]}",
                objectName="Meta",
            )
        )
        layout.addLayout(details)
        layout.addStretch(1)

        layout.addWidget(QLabel(f"{Decimal(order['grand_total'])} ج.م", objectName="Amount"))

        # Reopening only makes sense while it is still open; a paid order is
        # reprinted, not resumed.
        if order["status"] not in ("PAID", "VOIDED"):
            resume = QPushButton("فتح", objectName="Open")
            resume.clicked.connect(lambda _=False, o=order: self._reopen(o))
            layout.addWidget(resume)

        if self.session.can("orders.reprint"):
            reprint = QPushButton("إعادة طباعة", objectName="Reprint")
            reprint.clicked.connect(lambda _=False, o=order: self._reprint(o))
            layout.addWidget(reprint)

        return frame

    def _reopen(self, order: dict) -> None:
        self.reopen_requested.emit(order["id"])
        self.accept()

    def _reprint(self, order: dict) -> None:
        """
        Queued as a marked duplicate, through the same builder payment uses.

        Building it here from the row would give a second definition of what a
        receipt is, and the reprint would be the one that disagrees.
        """
        folded = service.load(self.db, order["id"])
        document = receipts.build(
            folded,
            header=self.header,
            cashier=self.session.full_name_ar,
            serial=order.get("invoice_serial") or order["local_number"],
        )
        spooler.enqueue(self.db, document)

        logger.info(
            "Receipt reprint queued",
            extra={"order": order["id"], "by": self.session.user_id},
        )
        self.message.setText(f"تمت إضافة إيصال {order['local_number']} لطابور الطباعة.")


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if (widget := item.widget()) is not None:
            widget.deleteLater()
