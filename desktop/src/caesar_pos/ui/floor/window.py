"""
The floor map.

Tables laid out the way the room is, because finding "table 7" by the shape of
the room is meaningfully faster than reading it off a list — and a waiter walking
back from the terrace is thinking in geometry, not in sort order.

Colour follows state and **never carries the meaning alone**: every tile also
says the state in words and shows the running total. The kitchen display made the
same choice, for the same two reasons — colour-blind staff, and the washed-out
screens these actually run on.
"""

from __future__ import annotations

import logging
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
from . import geometry
from .room import RoomWidget

logger = logging.getLogger(__name__)

TILE_MIN = 132

STYLESHEET = f"""
QPushButton#TableFree {{
    background: #ffffff; color: #0f172a;
    border: 2px solid #cbd5e1; border-radius: 12px;
    font-size: 15px; min-width: {TILE_MIN}px; min-height: {TILE_MIN}px;
}}
QPushButton#TableBusy {{
    background: #eff6ff; color: #0f172a;
    border: 2px solid #1d4e89; border-radius: 12px;
    font-size: 15px; min-width: {TILE_MIN}px; min-height: {TILE_MIN}px;
}}
QPushButton#TableReady {{
    background: #f0fdf4; color: #14532d;
    border: 2px solid #15803d; border-radius: 12px;
    font-size: 15px; min-width: {TILE_MIN}px; min-height: {TILE_MIN}px;
}}
QLabel#AreaTab {{ font-size: 15px; }}
QLabel#FloorEmpty {{ color: #64748b; font-size: 15px; }}
"""


def tables(db: Database, *, area_id: str | None = None) -> list[dict]:
    """
    Tables with whatever open order is on them.

    The join is against the LOCAL projection, so a table shows the order this
    terminal knows about. During an outage that is only this device's orders —
    which is the honest limitation the header states, not something to paper over.
    """
    where = "" if area_id is None else "WHERE t.area_id = ?"
    params = () if area_id is None else (area_id,)

    rows = db.query(
        f"""
        SELECT t.id, t.number, t.seats, t.area_id, t.status,
               t.pos_x, t.pos_y, t.shape, t.span_x, t.span_y, t.rotation,
               o.id AS order_id, o.status AS order_status, o.grand_total,
               o.guest_count AS guest_count
        FROM m_tables t
        LEFT JOIN l_orders o
               ON o.table_id = t.id
              AND o.status IN ('OPEN', 'IN_KITCHEN', 'READY', 'SERVED')
        {where}
        ORDER BY t.pos_y, t.pos_x, t.number
        """,  # noqa: S608 — `where` is a fixed fragment; the value is bound
        params,
    )

    result = []
    for row in rows:
        table = dict(row)
        # The party, not the furniture. A terminal that only knows "occupied"
        # cannot tell a waiter that a six-top has four chairs free, which is the
        # question they walked over to ask.
        guests = table.pop("guest_count", None)
        table["seated_count"] = min(int(guests or 0), table["seats"]) if table["order_id"] else 0
        result.append(table)
    return result


def areas(db: Database) -> list[dict]:
    return [dict(row) for row in db.query("SELECT id, name_ar FROM m_areas ORDER BY name_ar")]


class FloorWindow(QWidget):
    """Emits `table_chosen(table_id, order_id|None)`."""

    table_chosen = Signal(str, object)

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.area_id: str | None = None

        self.setStyleSheet(STYLESHEET)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.tabs = QHBoxLayout()
        holder = QWidget()
        holder.setLayout(self.tabs)
        layout.addWidget(holder)

        self.empty = QLabel("لا توجد طاولات — لم تتم المزامنة بعد.", objectName="FloorEmpty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        # The room, painted. A grid of buttons only claims to be a map; a waiter
        # matching the screen to what is in front of them needs the round table
        # to be round.
        self.room = RoomWidget()
        self.room.table_clicked.connect(self._chosen)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.room)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

        # Kept for the tests and for any caller that wants the flat list: the
        # room is a rendering of `visible_tables`, not a second source of truth.
        self.grid = QGridLayout()

        self.refresh()

    def _chosen(self, table_id: str) -> None:
        table = next((t for t in self.visible_tables if t["id"] == table_id), None)
        if table is not None:
            self.table_chosen.emit(table["id"], table["order_id"])

    # ── data ─────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._render_tabs()
        self._render_tables()

    def select_area(self, area_id: str | None) -> None:
        self.area_id = area_id
        self.refresh()

    @property
    def visible_tables(self) -> list[dict]:
        return tables(self.db, area_id=self.area_id)

    @property
    def occupied_count(self) -> int:
        return sum(1 for t in self.visible_tables if t["order_id"])

    # ── rendering ────────────────────────────────────────────────────────────

    def _render_tabs(self) -> None:
        _clear(self.tabs)

        for area_id, label in [(None, "الكل"), *[(a["id"], a["name_ar"]) for a in areas(self.db)]]:
            button = QPushButton(label)
            button.setObjectName("Secondary" if area_id != self.area_id else "")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, aid=area_id: self.select_area(aid))
            self.tabs.addWidget(button)

        self.tabs.addStretch(1)

    #: Words that mean "this area is outside". A guess, and a cheap one — the
    #: alternative is a column somebody has to remember to set, and getting it
    #: wrong costs a decking texture rather than anything that matters.
    OUTDOOR_WORDS = ("تراس", "خارج", "حديق", "جاردن", "terrace", "garden", "outdoor")

    @property
    def is_outdoor(self) -> bool:
        if self.area_id is None:
            return False
        name = next((a["name_ar"] for a in areas(self.db) if a["id"] == self.area_id), "")
        return any(word in name.lower() for word in self.OUTDOOR_WORDS)

    def _render_tables(self) -> None:
        rows = self.visible_tables
        self.empty.setVisible(not rows)
        self.room.show_tables(rows, outdoor=self.is_outdoor)

    @staticmethod
    def _label(table: dict) -> str:
        """
        The table in words. Painted onto the room, and read aloud by a screen
        reader — colour alone fails for colour-blind staff and on a washed-out
        screen.
        """
        seated = table.get("seated_count") or 0
        parts = [f"طاولة {table['number']}", f"{seated}/{table['seats']} كرسي"]

        if table["order_id"]:
            parts.append(_STATE_LABELS.get(table["order_status"], table["order_status"]))
            parts.append(f"{Decimal(table['grand_total'])} ج.م")
        else:
            parts.append(geometry.STATE_LABELS[geometry.FREE])

        return "\n".join(parts)

    @staticmethod
    def _style_for(table: dict) -> str:
        if not table["order_id"]:
            return "TableFree"
        if table["order_status"] in ("READY", "SERVED"):
            return "TableReady"
        return "TableBusy"


_STATE_LABELS = {
    "OPEN": "مفتوحة",
    "IN_KITCHEN": "في المطبخ",
    "READY": "جاهزة",
    "SERVED": "تم التقديم",
}


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if (widget := item.widget()) is not None:
            widget.deleteLater()
