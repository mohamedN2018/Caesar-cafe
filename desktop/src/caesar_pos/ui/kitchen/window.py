"""
The kitchen display.

Read across a hot, noisy room by someone whose hands are busy, so it is the
plainest screen in the product:

  * **Ticket number large.** Staff shout it. "Ninety-four" works; a UUID does not.
  * **Age, not clock time.** "12 minutes" is the number a cook acts on; "14:32"
    requires arithmetic while holding a pan.
  * **Colour and words together.** Green/amber/red *and* "متأخر" — the same rule
    as the floor map, for the same reasons.
  * **One tap advances a ticket.** No menus. A cook with one free hand should not
    be navigating.

The board reads the local mirror of kitchen tickets. When the WebSocket is
connected the server pushes; when it is not, this polls — and the header says
which, because a display quietly showing five-minute-old tickets is worse than
one that admits it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

CARD_MIN_WIDTH = 260

STYLESHEET = f"""
QWidget#TicketNew     {{ background: #ffffff; border: 3px solid #cbd5e1; border-radius: 12px; }}
QWidget#TicketWorking {{ background: #fffbeb; border: 3px solid #d97706; border-radius: 12px; }}
QWidget#TicketLate    {{ background: #fef2f2; border: 3px solid #b91c1c; border-radius: 12px; }}
QWidget#TicketReady   {{ background: #f0fdf4; border: 3px solid #15803d; border-radius: 12px; }}
QLabel#TicketNumber {{ font-size: 34px; font-weight: 800; }}
QLabel#TicketAge    {{ font-size: 22px; font-weight: 700; }}
QLabel#TicketState  {{ font-size: 15px; font-weight: 600; }}
QLabel#TicketLine   {{ font-size: 18px; }}
QLabel#TicketNote   {{ font-size: 15px; color: #92400e; font-weight: 600; }}
QLabel#KdsEmpty     {{ font-size: 18px; color: #64748b; }}
QWidget#TicketCard  {{ min-width: {CARD_MIN_WIDTH}px; }}
"""

STATE_LABELS = {
    "NEW": "جديدة",
    "ACCEPTED": "مقبولة",
    "PREPARING": "تحت التحضير",
    "READY": "جاهزة",
}

#: What one tap does next. Mirrors the server's ALLOWED_TRANSITIONS for the
#: forward path — the server refuses anything else, so this only has to be right
#: about the common case.
NEXT_ACTION = {
    "NEW": ("ابدأ التحضير", "PREPARING"),
    "ACCEPTED": ("ابدأ التحضير", "PREPARING"),
    "PREPARING": ("جاهزة", "READY"),
    "READY": ("تم التقديم", "SERVED"),
}


def age_minutes(created_at: str, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0, int((now - created).total_seconds() // 60))


class TicketCard(QWidget):
    """One station's share of one firing."""

    advance_requested = Signal(str, str)  # ticket_id, target status

    def __init__(
        self,
        ticket: dict,
        *,
        now: datetime | None = None,
        can_advance: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ticket = ticket
        self.setObjectName("TicketCard")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel(f"#{ticket['ticket_number']}", objectName="TicketNumber"))
        header.addStretch(1)

        minutes = age_minutes(ticket["created_at"], now=now)
        self.age = QLabel(f"{minutes} د", objectName="TicketAge")
        header.addWidget(self.age)
        layout.addLayout(header)

        target = ticket.get("target_minutes") or 0
        self.late = bool(target) and minutes > target

        state = QLabel(
            # Words beside the colour, always.
            ("متأخر · " if self.late else "")
            + STATE_LABELS.get(ticket["status"], ticket["status"]),
            objectName="TicketState",
        )
        layout.addWidget(state)

        if ticket.get("table"):
            layout.addWidget(QLabel(f"طاولة {ticket['table']}", objectName="TicketState"))

        for line in ticket.get("lines", []):
            quantity = str(line.get("quantity", "1")).rstrip("0").rstrip(".") or "1"
            layout.addWidget(QLabel(f"{quantity}×  {line['name']}", objectName="TicketLine"))

            for modifier in line.get("modifiers", []):
                layout.addWidget(QLabel(f"    + {modifier}", objectName="TicketLine"))
            if line.get("note"):
                layout.addWidget(QLabel(f"    ** {line['note']} **", objectName="TicketNote"))

        # Hidden, not disabled, when this person may not advance tickets. A
        # button that always refuses teaches staff to ignore refusals. The server
        # re-checks `kitchen.update_status` regardless — this is display only.
        label_action = NEXT_ACTION.get(ticket["status"]) if can_advance else None
        if label_action:
            label, target_status = label_action
            button = QPushButton(label)
            button.clicked.connect(
                lambda _=False: self.advance_requested.emit(ticket["id"], target_status)
            )
            layout.addWidget(button)

        self.setObjectName(self._style())

    def _style(self) -> str:
        if self.ticket["status"] == "READY":
            return "TicketReady"
        if self.late:
            return "TicketLate"
        if self.ticket["status"] == "PREPARING":
            return "TicketWorking"
        return "TicketNew"


class KitchenWindow(QWidget):
    """Emits `advance(ticket_id, status)`; the caller performs and re-renders."""

    advance = Signal(str, str)

    COLUMNS = 4

    def __init__(self, *, can_advance: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.can_advance = can_advance
        self.setStyleSheet(STYLESHEET)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setWindowTitle("شاشة المطبخ")

        self.tickets: list[dict] = []
        self.cards: list[TicketCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        self.title = QLabel("المطبخ", objectName="TicketNumber")
        header.addWidget(self.title)
        header.addStretch(1)
        # Says whether it is live or polling. A display quietly showing
        # five-minute-old tickets is worse than one that admits it.
        self.connection = QLabel("", objectName="TicketState")
        header.addWidget(self.connection)
        layout.addLayout(header)

        self.empty = QLabel("لا توجد تذاكر", objectName="KdsEmpty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        holder = QWidget()
        holder.setLayout(self.grid)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

    def show_tickets(self, tickets: list[dict], *, now: datetime | None = None) -> None:
        """
        Oldest first, always.

        A board sorted by anything else lets a ticket sit at the bottom while
        newer ones are made — which is precisely the failure a kitchen display
        exists to prevent.
        """
        self.tickets = sorted(tickets, key=lambda t: t.get("created_at", ""))
        _clear(self.grid)
        self.cards = []
        self.empty.setVisible(not self.tickets)

        for index, ticket in enumerate(self.tickets):
            card = TicketCard(ticket, now=now, can_advance=self.can_advance)
            card.advance_requested.connect(self.advance.emit)
            self.grid.addWidget(card, index // self.COLUMNS, index % self.COLUMNS)
            self.cards.append(card)

    def set_connection(self, *, live: bool) -> None:
        self.connection.setText("🟢 مباشر" if live else "🟡 تحديث دوري")

    @property
    def late_count(self) -> int:
        """
        Counted off the rendered cards, not recomputed from the clock.

        Two definitions of "late" drift apart the moment they are evaluated a
        second apart, and then the header says 3 while two cards are red. One
        definition, decided once at render.
        """
        return sum(1 for card in self.cards if card.late)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if (widget := item.widget()) is not None:
            widget.deleteLater()
