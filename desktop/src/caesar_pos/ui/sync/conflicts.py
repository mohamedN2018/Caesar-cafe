"""
The operations that need a person.

`outbox.mark_conflict` exists for one reason: some failures cannot be retried
into success. An order paid on another terminal will still be paid on the next
attempt, and retrying it forever only buries the failures that matter. So the
operation stops, and a human decides.

Until this screen existed, that human had nowhere to look. The header could say
"⚠️ تعارض (٢)" and there was no way to find out which two, what happened, or
what to do — which made the whole conflict path a dead end. The most careful
part of the sync design was invisible.

Two actions, and the difference between them is the whole screen:

  * **إعادة المحاولة** puts the operation back in the queue. For the cases that
    fix themselves — a missing event that has since arrived, a shift the server
    had not seen yet — this is all that is needed.
  * **تم التعامل معه** acknowledges it without retrying. For the cases a person
    resolved in the room: the items were re-rung on a new order, the customer
    was refunded, the child was collected. The operation is dead and saying so
    is how it stops nagging.

Neither one deletes anything. The row stays in the outbox with its error, so
"what happened on the 14th" is still answerable next month.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...local import outbox
from ...local.db import Database
from .. import palette as p

logger = logging.getLogger(__name__)

STYLESHEET = f"""
QLabel#Title    {{ font-size: 20px; font-weight: 700; }}
QLabel#Empty    {{ font-size: 15px; color: {p.INK_MUTED}; }}
QLabel#Code     {{ font-size: 12px; color: {p.INK_FAINT}; }}
QLabel#Message  {{ font-size: 15px; font-weight: 600; color: {p.INK}; }}
QLabel#Kind     {{ font-size: 12px; color: {p.INK_MUTED}; }}
QFrame#Card     {{ background: {p.SURFACE}; border: 1px solid {p.BORDER_STRONG};
                   border-radius: 10px; }}
QPushButton#Retry {{ background: {p.BRAND_700}; color: {p.FG_ON_BRAND}; padding: 8px 14px; }}
QPushButton#Ack   {{ background: {p.SURFACE_SUNKEN}; color: {p.INK}; padding: 8px 14px; }}
"""

#: What each operation was, in words a cashier recognises. The entity type is
#: how the server names it; nobody on the floor calls a sale a `payment` row.
ENTITY_LABELS = {
    "order_open": "فتح طلب",
    "order_event": "تعديل على طلب",
    "payment": "تحصيل دفعة",
    "refund": "استرجاع مبلغ",
    "shift_open": "فتح وردية",
    "shift_close": "إغلاق وردية",
    "cash_movement": "حركة نقدية",
    "waste": "تسجيل هالك",
    "play_check_in": "دخول طفل",
    "play_check_out": "خروج طفل",
}


class ConflictsDialog(QDialog):
    """Emits `resolved()` whenever something changes, so the header can update."""

    resolved = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db

        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("عمليات تحتاج مراجعة")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        layout.addWidget(QLabel("عمليات تحتاج مراجعة", objectName="Title"))
        layout.addWidget(
            QLabel(
                "دي عمليات الخادم رفضها ومش هتنجح لو اتبعتت تاني لوحدها. راجعها واحدة واحدة.",
                objectName="Kind",
            )
        )

        self.empty = QLabel("لا توجد تعارضات — كل شيء تمت مزامنته.", objectName="Empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self.list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

        close = QPushButton("إغلاق")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignLeft)

        self.refresh()

    @property
    def conflicts(self) -> list[dict]:
        return outbox.open_conflicts(self.db)

    def refresh(self) -> None:
        _clear(self.list)
        rows = self.conflicts
        self.empty.setVisible(not rows)

        for conflict in rows:
            self.list.addWidget(self._card(conflict))
        self.list.addStretch(1)

    def _card(self, conflict: dict) -> QFrame:
        card = QFrame(objectName="Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # The message the outbox wrote, which is already in words a cashier can
        # act on. Rewording it here would give one rule two vocabularies.
        layout.addWidget(QLabel(conflict["message_ar"], objectName="Message"))

        kind = ENTITY_LABELS.get(conflict["entity_type"], conflict["entity_type"])
        layout.addWidget(
            QLabel(f"{kind} · {conflict['seen_at'][:16].replace('T', ' ')}", objectName="Kind")
        )
        layout.addWidget(QLabel(conflict["code"], objectName="Code"))

        actions = QHBoxLayout()
        retry = QPushButton("إعادة المحاولة", objectName="Retry")
        retry.clicked.connect(lambda _=False, c=conflict: self._retry(c))
        actions.addWidget(retry)

        acknowledge = QPushButton("تم التعامل معه", objectName="Ack")
        acknowledge.clicked.connect(lambda _=False, c=conflict: self._acknowledge(c))
        actions.addWidget(acknowledge)
        actions.addStretch(1)
        layout.addLayout(actions)

        return card

    def _retry(self, conflict: dict) -> None:
        outbox.requeue(self.db, conflict["op_uuid"])
        outbox.acknowledge(self.db, conflict["op_uuid"])
        logger.info("Conflict requeued by a human", extra={"op": conflict["op_uuid"]})
        self.refresh()
        self.resolved.emit()

    def _acknowledge(self, conflict: dict) -> None:
        """
        Marked seen, NOT retried and NOT deleted.

        The row keeps its error, so "what happened on the 14th" is answerable
        next month. Deleting it would make the queue tidy and the history a lie.
        """
        outbox.acknowledge(self.db, conflict["op_uuid"])
        logger.info("Conflict acknowledged", extra={"op": conflict["op_uuid"]})
        self.refresh()
        self.resolved.emit()


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if (widget := item.widget()) is not None:
            widget.deleteLater()
