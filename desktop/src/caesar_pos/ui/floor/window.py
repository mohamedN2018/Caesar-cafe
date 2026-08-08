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
               t.pos_x, t.pos_y,
               o.id AS order_id, o.status AS order_status, o.grand_total
        FROM m_tables t
        LEFT JOIN l_orders o
               ON o.table_id = t.id
              AND o.status IN ('OPEN', 'IN_KITCHEN', 'READY', 'SERVED')
        {where}
        ORDER BY t.pos_y, t.pos_x, t.number
        """,  # noqa: S608 — `where` is a fixed fragment; the value is bound
        params,
    )
    return [dict(row) for row in rows]


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

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        grid_holder = QWidget()
        grid_holder.setLayout(self.grid)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_holder)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

        self.refresh()

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

    def _render_tables(self) -> None:
        _clear(self.grid)
        rows = self.visible_tables
        self.empty.setVisible(not rows)

        for index, table in enumerate(rows):
            button = QPushButton(self._label(table))
            button.setObjectName(self._style_for(table))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _=False, t=table: self.table_chosen.emit(t["id"], t["order_id"])
            )
            # Laid out by the admin's canvas coordinates when they exist, so the
            # screen matches the room. Falls back to a flow when they do not.
            row, column = (
                (table["pos_y"], table["pos_x"])
                if (table["pos_x"] or table["pos_y"])
                else (index // 5, index % 5)
            )
            self.grid.addWidget(button, row, column)

    @staticmethod
    def _label(table: dict) -> str:
        parts = [f"طاولة {table['number']}", f"{table['seats']} أفراد"]

        if table["order_id"]:
            # The state in words, next to the colour. Colour alone fails for
            # colour-blind staff and on a washed-out screen.
            parts.append(_STATE_LABELS.get(table["order_status"], table["order_status"]))
            parts.append(f"{Decimal(table['grand_total'])} ج.م")
        else:
            parts.append("متاحة")

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
