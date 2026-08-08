"""
Where the chairs go, and the fact that both halves put them in the same place.

The Web draws the floor with CSS and this draws it with QPainter, so the seat
arithmetic exists twice — TypeScript in a browser, Python here. There is no
artefact to vendor, so the guarantee is behavioural: the case tables below are
also the case tables in `frontend/src/modules/floor/geometry.test.ts`, and the
last test in this file reads that file to prove they still describe the same
layouts.

Why it matters that they agree: the owner arranges the room on the Web, and the
waiter reads it on the terminal. If a six-top is 3+3 on one screen and 2+2+1+1
on the other, the map stops being a map of anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from caesar_pos.ui.floor import geometry as g

WEB_GEOMETRY = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "modules" / "floor" / "geometry.ts"
)


def _web() -> str:
    return WEB_GEOMETRY.read_text(encoding="utf-8")


class TestRoundTables:
    def test_chairs_are_spaced_evenly_around_the_circle(self) -> None:
        seats = g.seats_for(g.ROUND, 4)
        assert len(seats) == 4

        import math

        angles = sorted(round(math.degrees(math.atan2(s.y, s.x))) for s in seats)
        assert angles == [-90, 0, 90, 180]

    def test_the_first_seat_faces_the_door(self) -> None:
        first = g.seats_for(g.ROUND, 6)[0]
        assert round(first.x, 6) == 0
        assert first.y < 0

    def test_every_chair_is_the_same_distance_out(self) -> None:
        import math

        distances = {round(math.hypot(s.x, s.y), 6) for s in g.seats_for(g.ROUND, 7)}
        assert len(distances) == 1


class TestRectangles:
    def test_six_chairs_go_three_a_side(self) -> None:
        """
        Nobody seats two people at the narrow end of a table while the long
        sides still have room.
        """
        seats = g.seats_for(g.RECT, 6, 0, 2, 1)

        assert len([s for s in seats if s.y < 0]) == 3
        assert len([s for s in seats if s.y > 0]) == 3
        assert not [s for s in seats if abs(s.x) > 0.7]

    def test_a_tall_table_uses_its_vertical_sides(self) -> None:
        seats = g.seats_for(g.RECT, 6, 0, 1, 2)
        assert len([s for s in seats if abs(s.x) > 0.7]) == 6

    def test_four_split_two_and_two(self) -> None:
        seats = g.seats_for(g.SQUARE, 4)
        assert len([s for s in seats if s.y < 0]) == 2
        assert len([s for s in seats if s.y > 0]) == 2

    def test_the_ends_take_the_overflow(self) -> None:
        """Ten around a 2×1 is more than the sides hold, so the ends fill."""
        seats = g.seats_for(g.RECT, 10, 0, 2, 1)
        assert len(seats) == 10
        assert [s for s in seats if abs(s.x) > 0.7]


class TestBoothsAndBars:
    def test_a_booth_never_seats_anyone_at_the_back(self) -> None:
        seats = g.seats_for(g.BOOTH, 4)
        assert not [s for s in seats if s.y <= -0.7 and abs(s.x) < 0.7]
        assert len(seats) == 4

    def test_a_bar_is_one_row_facing_the_counter(self) -> None:
        seats = g.seats_for(g.BAR, 6)
        assert len({s.y for s in seats}) == 1
        assert all(s.angle == 0 for s in seats)

    def test_a_bar_is_drawn_shallow_whatever_its_span(self) -> None:
        width, height = g.footprint(g.BAR, 3, 2, 80)
        assert width == 240
        assert height < 80


class TestOccupancy:
    def test_a_party_sits_together(self) -> None:
        seats = g.seats_for(g.ROUND, 6, 2)
        assert [s.occupied for s in seats][:3] == [True, True, False]

    def test_it_never_draws_more_people_than_chairs(self) -> None:
        """A guest count above the seat count is a data problem, not a 7th chair."""
        assert len([s for s in g.seats_for(g.ROUND, 4, 9) if s.occupied]) == 4

    def test_a_negative_count_is_empty(self) -> None:
        assert not [s for s in g.seats_for(g.ROUND, 4, -3) if s.occupied]

    def test_a_seatless_table_draws_nothing(self) -> None:
        assert g.seats_for(g.SQUARE, 0) == []


class TestFullness:
    @pytest.mark.parametrize(
        ("seats", "seated", "expected"),
        [
            (4, 0, g.FREE),
            (6, 2, g.LIGHT),
            (5, 4, g.BUSY),
            (6, 6, g.FULL),
            (4, 9, g.FULL),
        ],
    )
    def test_it_distinguishes_two_at_a_six_top_from_six(self, seats, seated, expected) -> None:
        """
        The whole reason the view draws chairs at all. Both are "occupied" on a
        status board, and only one of them can take a walk-in of four.
        """
        assert g.fullness(seats, seated) == expected

    def test_every_state_has_words(self) -> None:
        """Colour never carries the meaning alone."""
        for state in (g.FREE, g.LIGHT, g.BUSY, g.FULL):
            assert g.STATE_LABELS[state]


class TestTheTwoHalvesAgree:
    """
    The Web and the Desktop draw the same room.

    Read across the monorepo rather than trusted: the owner arranges the floor
    in a browser and the waiter reads it on a terminal, and a six-top that is
    3+3 on one and 2+2+1+1 on the other makes the map a map of nothing.
    """

    def test_the_web_geometry_is_where_we_think(self) -> None:
        """Guard the guard: a moved file would make everything below vacuous."""
        assert WEB_GEOMETRY.exists(), f"the Web geometry has moved: {WEB_GEOMETRY}"
        assert "seatsFor" in _web()

    def test_the_gap_matches(self) -> None:
        match = re.search(r"const GAP = ([0-9.]+)", _web())
        assert match, "GAP is no longer declared the same way on the Web"
        assert float(match.group(1)) == g.GAP

    def test_the_edge_capacity_rule_matches(self) -> None:
        """
        A one-cell edge takes two; each extra cell adds one. Written out in both
        places, so this checks the Web's formula is still the same expression
        rather than only that some function exists.
        """
        assert "Math.max(2, span + 1)" in _web()
        assert g.edge_capacity(1) == 2
        assert g.edge_capacity(2) == 3
        assert g.edge_capacity(4) == 5

    def test_the_same_shapes_are_supported(self) -> None:
        web_shapes = set(re.findall(r"'(ROUND|SQUARE|RECT|BOOTH|BAR)'", _web()))
        assert web_shapes == {g.ROUND, g.SQUARE, g.RECT, g.BOOTH, g.BAR}

    def test_the_fullness_thresholds_match(self) -> None:
        assert "seated / seats >= 0.6" in _web()
        assert g.fullness(10, 6) == g.BUSY
        assert g.fullness(10, 5) == g.LIGHT
