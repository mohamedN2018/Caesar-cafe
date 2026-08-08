"""
The room, painted.

The Web draws the floor with CSS 3D. Qt has no such thing, so this paints the
same room with QPainter — same geometry, same palette, same rule that colour
never carries a meaning alone.

Why paint it rather than lay out widgets: a round table with six chairs around
it is not a rectangle, and faking it with stylesheets means a grid of buttons
that only claims to be a map. A waiter using this is matching what is on the
screen to what is in front of them, and a square standing in for a round table
breaks that the first time they look up.

Depth is a drop shadow and a lifted edge rather than a perspective transform.
On the hardware these run on — an atom-class box driving a 15" touchscreen —
a tilted plane costs frames the till cannot spare, and the flat overhead view is
the one a floor plan is normally read in anyway.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import palette as p
from . import geometry

logger = logging.getLogger(__name__)

#: Pixels per grid cell. Large: this is a touchscreen and a table is a target.
CELL = 96
MARGIN = 24
CHAIR = 20

STATE_FILL = {
    geometry.FREE: p.TABLE_FREE,
    geometry.LIGHT: "#fdf0f1",
    geometry.BUSY: p.TABLE_BUSY,
    geometry.FULL: p.BRAND_300,
}


class RoomWidget(QWidget):
    """
    Draws tables and emits `table_clicked(table_id)`.

    `tables` are dicts as `floor.window.tables()` returns them, plus whatever
    occupancy the caller knows.
    """

    table_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tables: list[dict] = []
        self.columns = 10
        self.rows = 8
        self._hitboxes: list[tuple[QRectF, str]] = []
        self.setMinimumSize(640, 420)
        self.setMouseTracking(True)

    def show_tables(self, tables: list[dict]) -> None:
        self.tables = tables
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(self.columns * CELL + MARGIN * 2, self.rows * CELL + MARGIN * 2)

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._paint_floor(painter)
        self._hitboxes = []
        for table in self.tables:
            self._paint_table(painter, table)

        painter.end()

    def _paint_floor(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(p.FLOOR_TILE))

        # Tiles, so the room reads as a floor rather than a canvas. Cheap: two
        # loops of fillRect, no image to load or scale.
        alt = QColor(p.FLOOR_TILE_ALT)
        size = CELL // 2
        for row in range((self.height() // size) + 1):
            for column in range((self.width() // size) + 1):
                if (row + column) % 2 == 0:
                    painter.fillRect(column * size, row * size, size, size, alt)

    def _paint_table(self, painter: QPainter, table: dict) -> None:
        shape = table.get("shape") or geometry.SQUARE
        seats = int(table.get("seats") or 0)
        seated = int(table.get("seated_count") or 0)
        span_x = int(table.get("span_x") or 1)
        span_y = int(table.get("span_y") or 1)
        rotation = int(table.get("rotation") or 0)

        width, height = geometry.footprint(shape, span_x, span_y, CELL)
        centre = QPointF(
            MARGIN + int(table.get("pos_x") or 0) * CELL + width / 2,
            MARGIN + int(table.get("pos_y") or 0) * CELL + height / 2,
        )

        painter.save()
        painter.translate(centre)
        painter.rotate(rotation)

        for seat in geometry.seats_for(shape, seats, seated, span_x, span_y):
            self._paint_chair(painter, seat, width, height)

        body = QRectF(-width / 2, -height / 2, width, height)
        self._paint_top(painter, body, shape, table, seats, seated)

        painter.restore()

        # The hitbox is unrotated and axis-aligned. A rotated one would be
        # more correct and would cost a polygon test per click on a machine
        # that is also folding an order; 15° of slop on a 96px target is
        # nothing a fingertip notices.
        self._hitboxes.append(
            (
                QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height),
                str(table.get("id") or table.get("table_id")),
            )
        )

    def _paint_chair(
        self, painter: QPainter, seat: geometry.Seat, width: float, height: float
    ) -> None:
        colour = QColor(p.CHAIR_OCCUPIED if seat.occupied else p.CHAIR)
        rect = QRectF(
            seat.x * width * 0.5 - CHAIR / 2,
            seat.y * height * 0.5 - CHAIR / 2,
            CHAIR,
            CHAIR,
        )

        # A lifted edge under each chair. Occupied ones sit taller, so a full
        # table reads as full before anybody has read a number.
        lift = 6 if seat.occupied else 3
        shadow = QColor(p.BRAND_900 if seat.occupied else p.WOOD_EDGE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow))
        painter.drawRoundedRect(rect.translated(0, lift), 5, 5)
        painter.setBrush(QBrush(colour))
        painter.drawRoundedRect(rect, 5, 5)

    def _paint_top(
        self, painter: QPainter, body: QRectF, shape: str, table: dict, seats: int, seated: int
    ) -> None:
        state = (
            "cleaning" if table.get("status") == "CLEANING" else geometry.fullness(seats, seated)
        )
        fill = QColor(p.WARNING_BG if state == "cleaning" else STATE_FILL.get(state, p.TABLE_FREE))

        path = QPainterPath()
        if shape == geometry.ROUND:
            path.addEllipse(body)
        elif shape == geometry.BOOTH:
            path.addRoundedRect(body, 14, 14)
        else:
            path.addRoundedRect(body, 10, 10)

        # The lift: a solid dark edge below, then the top. This is what makes it
        # read as furniture standing on a floor rather than a sticker printed
        # on it.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(p.WOOD_DARK)))
        painter.drawPath(path.translated(0, 8))

        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(QColor(p.WOOD_EDGE), 2))
        painter.drawPath(path)

        painter.save()
        # Counter-rotate, so the number stays upright however the table is turned.
        painter.rotate(-int(table.get("rotation") or 0))

        number = QFont(painter.font())
        number.setPointSize(15)
        number.setBold(True)
        painter.setFont(number)
        painter.setPen(QPen(QColor(p.INK)))
        painter.drawText(
            QRectF(body.x(), body.y() - 8, body.width(), body.height()),
            Qt.AlignmentFlag.AlignCenter,
            str(table.get("number", "")),
        )

        detail = QFont(painter.font())
        detail.setPointSize(9)
        detail.setBold(False)
        painter.setFont(detail)
        painter.setPen(QPen(QColor(p.INK_MUTED)))

        # Seats AND money, both in words. The occupancy is what seats a walk-in;
        # the total is what a waiter came over to find.
        due = table.get("grand_total")
        caption = f"{seated}/{seats}"
        if table.get("order_id") and due:
            caption = f"{caption} · {Decimal(str(due))}"

        painter.drawText(
            QRectF(body.x(), body.y() + 14, body.width(), body.height()),
            Qt.AlignmentFlag.AlignCenter,
            caption,
        )
        painter.restore()

    # ── input ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        point = event.position()
        # Reversed, so the topmost drawn table wins an overlap — which is the
        # one the eye picked.
        for rect, table_id in reversed(self._hitboxes):
            if rect.contains(point):
                self.table_clicked.emit(table_id)
                return
