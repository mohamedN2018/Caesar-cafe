"""
The kids-area board.

The one screen in this product whose failure is not financial. A child is
physically present, and the questions it has to answer instantly are "who is
inside", "whose child is that", and "who may collect them".

So, unlike every other board here:

  * **The guardian's name and phone are on the card**, not behind a tap. Staff
    need them while looking at the child, not after navigating.
  * **Medical notes are shown, always, in a colour that interrupts.** An allergy
    hidden one tap away is an allergy nobody reads.
  * **The running charge is displayed but never computed here.** It comes from
    the vendored `play_pricing`, the same module the server runs.
  * **Capacity is a hard number in the header** — a safety limit, not a metric.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

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

from ...local.db import Database
from ...vendored.play_pricing import Tariff, compute_charge, elapsed_minutes

logger = logging.getLogger(__name__)

STYLESHEET = """
QWidget#SessionOk   { background: #f0fdf4; border: 3px solid #15803d; border-radius: 12px; }
QWidget#SessionWarn { background: #fffbeb; border: 3px solid #d97706; border-radius: 12px; }
QWidget#SessionLate { background: #fef2f2; border: 3px solid #b91c1c; border-radius: 12px; }
QLabel#Tag      { font-size: 26px; font-weight: 800; }
QLabel#Child    { font-size: 20px; font-weight: 700; }
QLabel#Guardian { font-size: 15px; }
QLabel#Elapsed  { font-size: 22px; font-weight: 700; }
QLabel#Charge   { font-size: 18px; font-weight: 600; }
QLabel#Medical  { font-size: 15px; font-weight: 700; color: #92400e;
                  background: #fef3c7; border-radius: 6px; padding: 6px; }
QLabel#Capacity { font-size: 20px; font-weight: 700; }
QLabel#Full     { font-size: 20px; font-weight: 800; color: #b91c1c; }
QLabel#KidsEmpty{ font-size: 18px; color: #64748b; }
"""

#: Minutes before the expected end at which a card turns amber.
WARN_BEFORE_MINUTES = 10


def open_sessions(db: Database) -> list[dict]:
    return [
        dict(row)
        for row in db.query(
            "SELECT * FROM l_play_sessions WHERE status IN ('ACTIVE', 'OVERDUE') "
            "ORDER BY checked_in_at"
        )
    ]


def running_charge(session: dict, *, now: datetime | None = None) -> Decimal:
    """
    What the visit costs if the child leaves now.

    Computed from the SNAPSHOTTED tariff with the vendored engine — the same
    module the server runs, so the figure on this board is the figure on the
    bill. A tariff edited while the child is playing does not re-price the visit.
    """
    snapshot = json.loads(session.get("tariff_snapshot") or "{}")
    if not snapshot:
        return Decimal("0.00")

    tariff = Tariff(
        mode=snapshot["mode"],
        entry_fee=Decimal(snapshot["entry_fee"]),
        included_minutes=int(snapshot.get("included_minutes", 0)),
        package_minutes=int(snapshot.get("package_minutes", 0)),
        block_minutes=int(snapshot.get("block_minutes", 0)),
        block_rate=Decimal(snapshot.get("block_rate", "0")),
        grace_minutes=int(snapshot.get("grace_minutes", 0)),
        daily_cap=Decimal(snapshot.get("daily_cap", "0")),
    )
    checked_in = datetime.fromisoformat(session["checked_in_at"])
    if checked_in.tzinfo is None:
        checked_in = checked_in.replace(tzinfo=UTC)

    minutes = elapsed_minutes(checked_in, now or datetime.now(UTC))
    return compute_charge(tariff, minutes).charge


def elapsed(session: dict, *, now: datetime | None = None) -> int:
    checked_in = datetime.fromisoformat(session["checked_in_at"])
    if checked_in.tzinfo is None:
        checked_in = checked_in.replace(tzinfo=UTC)
    return elapsed_minutes(checked_in, now or datetime.now(UTC))


class SessionCard(QWidget):
    checkout_requested = Signal(str)

    def __init__(self, session: dict, *, now: datetime | None = None, parent=None) -> None:
        super().__init__(parent)
        self.session = session

        minutes = elapsed(session, now=now)
        self.overdue = session["status"] == "OVERDUE"

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(QLabel(f"#{session['tag_number']}", objectName="Tag"))
        header.addStretch(1)
        header.addWidget(QLabel(f"{minutes // 60}:{minutes % 60:02d}", objectName="Elapsed"))
        layout.addLayout(header)

        layout.addWidget(QLabel(session["child_name"], objectName="Child"))

        # Name AND phone on the card. Staff need them while looking at the
        # child, not after navigating to a detail screen.
        guardian = session["guardian_name"]
        if session.get("guardian_phone"):
            guardian = f"{guardian} · {session['guardian_phone']}"
        layout.addWidget(QLabel(f"ولي الأمر: {guardian}", objectName="Guardian"))

        layout.addWidget(QLabel(f"{running_charge(session, now=now)} ج.م", objectName="Charge"))

        if self.overdue:
            layout.addWidget(QLabel("⚠ تجاوز الوقت", objectName="Guardian"))

        if session.get("medical_notes"):
            # Always visible, in a colour that interrupts. An allergy one tap
            # away is an allergy nobody reads.
            layout.addWidget(QLabel(f"⚕ {session['medical_notes']}", objectName="Medical"))

        button = QPushButton("خروج")
        button.clicked.connect(lambda: self.checkout_requested.emit(session["id"]))
        layout.addWidget(button)

        self.setObjectName("SessionLate" if self.overdue else "SessionOk")


class KidsWindow(QWidget):
    """Emits `checkout_requested(session_id)` and `checkin_requested()`."""

    checkout_requested = Signal(str)
    checkin_requested = Signal()

    COLUMNS = 3

    def __init__(self, db: Database, *, capacity: int = 25, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.capacity = capacity

        self.setStyleSheet(STYLESHEET)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setWindowTitle("صالة الأطفال")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        self.capacity_label = QLabel("", objectName="Capacity")
        header.addWidget(self.capacity_label)
        self.full_label = QLabel("الصالة ممتلئة", objectName="Full")
        self.full_label.hide()
        header.addWidget(self.full_label)
        header.addStretch(1)

        self.checkin_button = QPushButton("دخول جديد")
        self.checkin_button.clicked.connect(self.checkin_requested.emit)
        header.addWidget(self.checkin_button)
        layout.addLayout(header)

        self.empty = QLabel("الصالة فارغة", objectName="KidsEmpty")
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

        self.refresh()

    def refresh(self, *, now: datetime | None = None) -> None:
        sessions = open_sessions(self.db)
        _clear(self.grid)

        self.empty.setVisible(not sessions)
        self.capacity_label.setText(f"الإشغال {len(sessions)} / {self.capacity}")

        # A hard limit, stated plainly. Capacity here is a safety rule, not a
        # metric, and the check-in button is disabled rather than failing later.
        at_capacity = len(sessions) >= self.capacity
        self.full_label.setVisible(at_capacity)
        self.checkin_button.setEnabled(not at_capacity)

        for index, session in enumerate(sessions):
            card = SessionCard(session, now=now)
            card.checkout_requested.connect(self.checkout_requested.emit)
            self.grid.addWidget(card, index // self.COLUMNS, index % self.COLUMNS)

    @property
    def occupancy(self) -> int:
        return len(open_sessions(self.db))

    @property
    def is_full(self) -> bool:
        return self.occupancy >= self.capacity


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if (widget := item.widget()) is not None:
            widget.deleteLater()
