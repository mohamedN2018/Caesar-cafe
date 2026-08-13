"""
The room, painted.

The Web draws the floor with CSS 3D. Qt has no such thing, so this paints the
same room with QPainter — same geometry, same palette, same rule that colour
never carries a meaning alone.

Why paint it rather than lay out widgets: a round table with six chairs around
it is not a rectangle, and faking it with stylesheets gives a grid of buttons
that only claims to be a map. A waiter is matching what is on the screen to what
is in front of them, and a square standing in for a round table breaks that the
first time they look up.

Three things make it read as a room:

  * **Depth order.** Far tables first, and within a table the near chairs are
    painted over the top while the far ones go under it.
  * **Chairs shaped like chairs**, with a back turned away from the table.
  * **People at occupied seats.** "How many are actually on it" is the question
    this screen answers, and a figure answers it before a number does.

Depth is a soft shadow and a lifted edge rather than a perspective transform. On
the hardware these run on — an atom-class box driving a 15" touchscreen — a
tilted plane costs frames the till cannot spare, and a floor plan is normally
read from overhead anyway.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from .. import palette as p
from . import geometry

logger = logging.getLogger(__name__)

#: Pixels per grid cell. Large: this is a touchscreen and a table is a target.
CELL = 96
MARGIN = 28
CHAIR = 24

STATE_FILL = {
    geometry.FREE: p.TABLE_FREE,
    geometry.LIGHT: "#fdf0f1",
    geometry.BUSY: p.TABLE_BUSY,
    geometry.FULL: p.BRAND_300,
}

SKIN = "#d8a273"
SKIN_EDGE = "#a9764b"


class RoomWidget(QWidget):
    """
    Draws tables and emits `table_clicked(table_id)`.

    `tables` are dicts as `floor.window.tables()` returns them, carrying
    whatever occupancy the caller knows.
    """

    table_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tables: list[dict] = []
        self.columns = 10
        self.rows = 8
        self.outdoor = False
        self._hitboxes: list[tuple[QRectF, str]] = []
        self.setMinimumSize(640, 420)

    def show_tables(self, tables: list[dict], *, outdoor: bool = False) -> None:
        # Far tables first, so a near one overlaps it. `pos_y` alone is not
        # enough — a tall table one row back can still reach in front of the one
        # below it, so the sort is on where its near edge actually falls.
        self.tables = sorted(
            tables,
            key=lambda t: (
                int(t.get("pos_y") or 0) + int(t.get("span_y") or 1) * 0.5,
                int(t.get("pos_x") or 0),
            ),
        )
        self.outdoor = outdoor
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
        if self.outdoor:
            # Decking, so outside reads as outside before anybody reads a tab.
            painter.fillRect(self.rect(), QColor("#b98a5c"))
            plank = QColor("#ad7f52")
            for x in range(0, self.width(), 36):
                painter.fillRect(x, 0, 2, self.height(), plank)
            return

        painter.fillRect(self.rect(), QColor(p.FLOOR_TILE))
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

        self._paint_shadow(painter, width, height)
        painter.rotate(rotation)

        behind, infront = geometry.split_by_depth(
            geometry.seats_for(shape, seats, seated, span_x, span_y)
        )
        for seat in behind:
            self._paint_chair(painter, seat, width, height, rotation)

        body = QRectF(-width / 2, -height / 2, width, height)
        self._paint_top(painter, body, shape, table, seats, seated)

        # Painted last, so a chair pulled out towards the viewer sits over the
        # table rather than under it.
        for seat in infront:
            self._paint_chair(painter, seat, width, height, rotation)

        painter.restore()

        # The hitbox is unrotated and axis-aligned. A rotated one would be more
        # correct and would cost a polygon test per click on a machine that is
        # also folding an order; 15° of slop on a 96px target is nothing a
        # fingertip notices.
        self._hitboxes.append(
            (
                QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height),
                str(table.get("id") or table.get("table_id")),
            )
        )

    def _paint_shadow(self, painter: QPainter, width: float, height: float) -> None:
        """A soft ellipse on the floor — contact, rather than a hard offset."""
        rect = QRectF(-width * 0.62, height * 0.04, width * 1.24, height * 0.62)
        gradient = QRadialGradient(rect.center(), rect.width() / 2)
        gradient.setColorAt(0.0, QColor(42, 26, 22, 92))
        gradient.setColorAt(1.0, QColor(42, 26, 22, 0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)

    def _paint_chair(
        self, painter: QPainter, seat: geometry.Seat, width: float, height: float, rotation: int
    ) -> None:
        painter.save()
        painter.translate(seat.x * width * 0.5, seat.y * height * 0.5)
        painter.rotate(seat.angle)

        taken = seat.occupied
        painter.setPen(Qt.PenStyle.NoPen)

        # The back, turned away from the table. A block beside a table is a
        # block; a back and a seat is a chair.
        back = QRectF(-CHAIR / 2, -CHAIR / 2, CHAIR, CHAIR * 0.34)
        painter.setBrush(QBrush(QColor(p.BRAND_400 if taken else "#9a6739")))
        painter.drawRoundedRect(back, 4, 4)

        pad = QRectF(-CHAIR / 2 + 2, -CHAIR / 2 + 7, CHAIR - 4, CHAIR - 9)
        painter.setBrush(QBrush(QColor(p.BRAND_900 if taken else p.WOOD_EDGE)))
        painter.drawRoundedRect(pad.translated(0, 3), 4, 4)
        painter.setBrush(QBrush(QColor(p.CHAIR_OCCUPIED if taken else p.CHAIR)))
        painter.drawRoundedRect(pad, 4, 4)

        if taken:
            self._paint_person(painter)

        painter.restore()

    def _paint_person(self, painter: QPainter) -> None:
        """Head and shoulders. The count made visible before it is read."""
        painter.setPen(Qt.PenStyle.NoPen)

        body = QRectF(-8, -2, 16, 13)
        painter.setBrush(QBrush(QColor(p.BRAND_700)))
        painter.drawRoundedRect(body, 7, 7)

        painter.setBrush(QBrush(QColor(SKIN)))
        painter.setPen(QPen(QColor(SKIN_EDGE), 1.4))
        painter.drawEllipse(QRectF(-5.5, -9, 11, 11))

    def _paint_top(
        self, painter: QPainter, body: QRectF, shape: str, table: dict, seats: int, seated: int
    ) -> None:
        state = (
            "cleaning" if table.get("status") == "CLEANING" else geometry.fullness(seats, seated)
        )

        path = QPainterPath()
        if shape == geometry.ROUND:
            path.addEllipse(body)
        elif shape == geometry.BOOTH:
            path.addRoundedRect(body, 14, 14)
        else:
            path.addRoundedRect(body, 9, 9)

        # The thickness of the table, then the top. Together they read as an
        # object standing on the floor rather than printed on it.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(p.WOOD_DARK)))
        painter.drawPath(path.translated(0, 8))

        if state == "cleaning":
            painter.setBrush(QBrush(QColor(p.WARNING_BG)))
        else:
            top = QColor(STATE_FILL.get(state, p.TABLE_FREE))
            gradient = QLinearGradient(body.topLeft(), body.bottomRight())
            gradient.setColorAt(0.0, top.lighter(107))
            gradient.setColorAt(1.0, top.darker(106))
            painter.setBrush(QBrush(gradient))

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
        detail.setBold(True)
        painter.setFont(detail)
        painter.setPen(QPen(QColor(p.INK_MUTED)))

        # Seats AND money. The occupancy is what seats a walk-in; the total is
        # what a waiter came over to find.
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
