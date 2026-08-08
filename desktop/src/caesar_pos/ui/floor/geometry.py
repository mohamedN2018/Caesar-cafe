"""
Where the chairs go — the Desktop's copy of the Web's seat arithmetic.

Deliberately a copy rather than a shared module: the Web's version is
TypeScript running in a browser and this one is Python driving QPainter, so
there is no artefact to vendor. What IS shared is the behaviour, and
`tests/test_floor_geometry.py` asserts the two agree case for case — the same
discipline the money modules follow, enforced by the same kind of test.

The rules are the ones a person laying out furniture would use:

  * **Round tables** space their chairs evenly around the circle.
  * **Rectangles** put chairs on the long sides first and use the ends only when
    the sides are full. Six around a 2×1 table is 3+3, not 2+2+1+1, because
    nobody seats two people at the narrow end while the sides have room.
  * **Booths** are against a wall: three sides, never the back.
  * **Bars** are one row facing the counter.

Occupancy fills seats in order. A party of two at a six-top sits together, and
scattering them would draw something that does not happen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ROUND = "ROUND"
SQUARE = "SQUARE"
RECT = "RECT"
BOOTH = "BOOTH"
BAR = "BAR"

#: How far outside the table edge a chair sits, as a fraction of the table.
GAP = 0.78


@dataclass(frozen=True)
class Seat:
    """Position as a fraction of the table's own size, measured from its centre."""

    x: float
    y: float
    angle: float
    occupied: bool


def edge_capacity(span: int) -> int:
    """
    How many chairs fit along an edge `span` grid cells long.

    A one-cell edge takes two people; each extra cell adds one. Not a formula
    from anywhere — it is what a 60cm chair against a 70cm table edge comes to,
    and it produces the layouts people recognise.
    """
    return max(2, span + 1)


def _ring(count: int, occupied: int) -> list[Seat]:
    seats = []
    for index in range(count):
        # Start at the top and go clockwise, so seat one is the one a person
        # would call "facing the door" rather than an arbitrary point.
        angle = (index / count) * 360 - 90
        radians = math.radians(angle)
        seats.append(
            Seat(
                x=math.cos(radians) * GAP,
                y=math.sin(radians) * GAP,
                angle=angle + 90,
                occupied=index < occupied,
            )
        )
    return seats


def _perimeter(count: int, span_x: int, span_y: int, occupied: int) -> list[Seat]:
    horizontal_is_long = span_x >= span_y
    long_span = span_x if horizontal_is_long else span_y
    short_span = span_y if horizontal_is_long else span_x

    per_long_side = edge_capacity(long_span)
    on_sides = min(count, per_long_side * 2)
    first_side = math.ceil(on_sides / 2)
    second_side = on_sides - first_side

    overflow = count - on_sides
    per_end_cap = edge_capacity(short_span)
    first_end = min(per_end_cap, math.ceil(overflow / 2))
    second_end = min(per_end_cap, overflow - first_end)

    seats: list[Seat] = []

    def place(n: int, side: str) -> None:
        for index in range(n):
            t = (index + 1) / (n + 1) - 0.5
            if side == "top":
                seats.append(Seat(t * 1.5, -GAP, 180, False))
            elif side == "bottom":
                seats.append(Seat(t * 1.5, GAP, 0, False))
            elif side == "left":
                seats.append(Seat(-GAP, t * 1.5, 90, False))
            else:
                seats.append(Seat(GAP, t * 1.5, 270, False))

    if horizontal_is_long:
        place(first_side, "top")
        place(second_side, "bottom")
        place(first_end, "left")
        place(second_end, "right")
    else:
        place(first_side, "left")
        place(second_side, "right")
        place(first_end, "top")
        place(second_end, "bottom")

    return [Seat(seat.x, seat.y, seat.angle, index < occupied) for index, seat in enumerate(seats)]


def seats_for(
    shape: str, count: int, occupied: int = 0, span_x: int = 1, span_y: int = 1
) -> list[Seat]:
    if count <= 0:
        return []
    seated = max(0, min(occupied, count))

    if shape == ROUND:
        return _ring(count, seated)

    if shape == BAR:
        # One row facing the counter. A bar with chairs behind it is a table.
        return [
            Seat(((index + 1) / (count + 1) - 0.5) * 1.7, GAP, 0, index < seated)
            for index in range(count)
        ]

    if shape == BOOTH:
        # Against a wall: three sides, never the back.
        per_side = math.ceil(count / 3)
        seats: list[Seat] = []
        for index in range(count):
            side = index // per_side
            t = (index % per_side + 1) / (per_side + 1) - 0.5
            if side == 0:
                seats.append(Seat(t * 1.5, GAP, 0, False))
            elif side == 1:
                seats.append(Seat(-GAP, t * 1.5, 90, False))
            else:
                seats.append(Seat(GAP, t * 1.5, 270, False))
        return [Seat(s.x, s.y, s.angle, i < seated) for i, s in enumerate(seats)]

    return _perimeter(count, span_x, span_y, seated)


def footprint(shape: str, span_x: int, span_y: int, cell: int) -> tuple[int, int]:
    """Pixel size of a table before rotation."""
    width = span_x * cell
    height = span_y * cell
    if shape == BAR:
        # A bar is a counter: long and shallow, whatever its span says.
        return width, round(cell * 0.42)
    return width, height


FREE = "free"
LIGHT = "light"
BUSY = "busy"
FULL = "full"

STATE_LABELS = {
    FREE: "متاحة",
    LIGHT: "جالسون قليل",
    BUSY: "شبه ممتلئة",
    FULL: "ممتلئة",
}


def fullness(seats: int, seated: int) -> str:
    """
    How full a table is, as a word.

    Used for colour AND said in the label. "4 كراسي، 2 جالسين" is what a waiter
    seating a walk-in needs; a table that merely reads "occupied" hides two
    empty chairs.
    """
    if seated <= 0:
        return FREE
    if seated >= seats:
        return FULL
    return BUSY if seated / seats >= 0.6 else LIGHT
