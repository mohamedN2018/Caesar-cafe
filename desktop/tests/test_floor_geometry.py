"""
Where the chairs go on the Desktop floor map.

**This used to be half of a cross-language parity guard**, and the other half is
gone. The Web drew the same room in CSS from `frontend/src/modules/floor/
geometry.ts`, and the last class in this file read that file to prove a six-top
was 3+3 on both screens rather than 3+3 on one and 2+2+1+1 on the other.

The Web no longer draws a room. It shows a card per table and whether anybody is
on it, which is the question that screen was actually opened to answer, so the
TypeScript half was deleted rather than kept alive to satisfy a test — a guard
whose only remaining purpose is to keep dead code from being deleted has
inverted into the thing it was written to prevent.

What is left is the Desktop's own arithmetic, which `ui/floor/room.py` still
draws from. If a browser ever draws the room again, the parity class comes back
with it; it is in the history at the commit that removed the Web floor plan.
"""

from __future__ import annotations

import pytest

from caesar_pos.ui.floor import geometry as g


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
