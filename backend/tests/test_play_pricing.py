"""
The play-tariff golden file.

Same contract as `test_money.py`: every case in
tests/fixtures/play_tariff_cases.json was worked out by hand from the tables in
docs/12-kids-area.md, and the Desktop runs this same file against its vendored
copy of the module. A disagreement of one piaster between the two is a failed
build, not a support ticket six weeks later.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

import pytest

from apps.core.play_pricing import (
    MODE_OPEN_DAY,
    Tariff,
    compute_charge,
    elapsed_minutes,
    expected_end,
    tariff_applies_at,
)

FIXTURE = Path(__file__).parent / "fixtures" / "play_tariff_cases.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _tariff(name: str) -> Tariff:
    raw = {k: v for k, v in DATA["tariffs"][name].items() if not k.startswith("_")}
    return Tariff(
        mode=raw["mode"],
        entry_fee=Decimal(raw["entry_fee"]),
        included_minutes=int(raw.get("included_minutes", 0)),
        package_minutes=int(raw.get("package_minutes", 0)),
        block_minutes=int(raw.get("block_minutes", 0)),
        block_rate=Decimal(raw.get("block_rate", "0")),
        grace_minutes=int(raw.get("grace_minutes", 0)),
        daily_cap=Decimal(raw.get("daily_cap", "0")),
    )


def _minutes(case: dict) -> int:
    if "minutes" in case:
        return case["minutes"]
    return elapsed_minutes(
        datetime.fromisoformat(case["start"]), datetime.fromisoformat(case["end"])
    )


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["name"] for c in DATA["cases"]])
def test_golden_case(case: dict) -> None:
    result = compute_charge(
        _tariff(case["tariff"]),
        _minutes(case),
        rounding=case.get("rounding", "up_to_block"),
        grace_minutes=case.get("grace_minutes"),
    )
    expected = case["expect"]

    assert result.charge == Decimal(expected["charge"]), case.get("why", case["name"])
    assert result.billable_minutes == expected["billable_minutes"]
    assert result.blocks == expected["blocks"]
    assert result.capped is expected["capped"]


def test_the_fixture_is_not_empty() -> None:
    """A golden file that silently emptied would pass every test above."""
    assert len(DATA["cases"]) >= 25


class TestElapsed:
    def test_crossing_midnight_is_not_a_special_case(self) -> None:
        start = datetime.fromisoformat("2026-08-07T23:40:00+03:00")
        end = datetime.fromisoformat("2026-08-08T00:56:00+03:00")
        assert elapsed_minutes(start, end) == 76

    def test_seconds_are_floored(self) -> None:
        """34:59 is 34 minutes. Rounding a second into a charge starts arguments."""
        start = datetime.fromisoformat("2026-08-07T14:00:00+03:00")
        end = datetime.fromisoformat("2026-08-07T14:34:59+03:00")
        assert elapsed_minutes(start, end) == 34

    def test_a_backwards_clock_yields_zero_not_a_credit(self) -> None:
        start = datetime.fromisoformat("2026-08-07T15:00:00+03:00")
        end = datetime.fromisoformat("2026-08-07T14:00:00+03:00")
        assert elapsed_minutes(start, end) == 0


class TestExpectedEnd:
    def test_timed_ends_after_the_included_period(self) -> None:
        start = datetime.fromisoformat("2026-08-07T14:00:00+03:00")
        assert expected_end(_tariff("HOUR_METER"), start).hour == 14
        assert expected_end(_tariff("HOUR_METER"), start).minute == 30

    def test_package_ends_after_the_package(self) -> None:
        start = datetime.fromisoformat("2026-08-07T14:00:00+03:00")
        assert expected_end(_tariff("PLAY_HOUR"), start).hour == 15

    def test_open_day_has_no_end(self) -> None:
        """The branch's closing time is the caller's business, not this module's."""
        start = datetime.fromisoformat("2026-08-07T14:00:00+03:00")
        assert expected_end(_tariff("OPEN_DAY"), start) is None
        assert _tariff("OPEN_DAY").mode == MODE_OPEN_DAY


class TestWindows:
    def test_no_window_means_always(self) -> None:
        moment = datetime.fromisoformat("2026-08-07T03:00:00+03:00")
        assert tariff_applies_at(moment, applies_days=[], applies_from=None, applies_to=None)

    def test_a_daytime_window(self) -> None:
        inside = datetime.fromisoformat("2026-08-07T15:00:00+03:00")
        outside = datetime.fromisoformat("2026-08-07T21:00:00+03:00")
        window = {"applies_days": [], "applies_from": time(12, 0), "applies_to": time(18, 0)}

        assert tariff_applies_at(inside, **window)
        assert not tariff_applies_at(outside, **window)

    def test_a_window_that_wraps_past_midnight(self) -> None:
        """A late-night rate of 22:00–02:00 is a real thing a cafe sells."""
        window = {"applies_days": [], "applies_from": time(22, 0), "applies_to": time(2, 0)}

        assert tariff_applies_at(datetime.fromisoformat("2026-08-07T23:30:00+03:00"), **window)
        assert tariff_applies_at(datetime.fromisoformat("2026-08-08T01:30:00+03:00"), **window)
        assert not tariff_applies_at(datetime.fromisoformat("2026-08-07T15:00:00+03:00"), **window)

    def test_day_of_week_filtering(self) -> None:
        friday = datetime.fromisoformat("2026-08-07T15:00:00+03:00")
        assert friday.weekday() == 4

        assert tariff_applies_at(friday, applies_days=[4, 5], applies_from=None, applies_to=None)
        assert not tariff_applies_at(
            friday, applies_days=[0, 1], applies_from=None, applies_to=None
        )


class TestGuards:
    def test_an_unknown_rounding_mode_is_rejected(self) -> None:
        """Silently falling back would price a whole branch wrong and say nothing."""
        with pytest.raises(ValueError, match="rounding"):
            compute_charge(_tariff("HOUR_METER"), 60, rounding="round_however")

    def test_negative_minutes_are_clamped(self) -> None:
        result = compute_charge(_tariff("HOUR_METER"), -30)
        assert result.charge == Decimal("25.00")
        assert result.elapsed_minutes == 0

    def test_a_tariff_with_no_block_rate_never_charges_overrun(self) -> None:
        """
        Configuration, not a bug: an area may want a flat fee with a soft end.
        The serializer refuses to CREATE this, but the engine must be total.
        """
        flat = Tariff(mode="TIMED", entry_fee=Decimal("30.00"), included_minutes=60)
        assert compute_charge(flat, 300).charge == Decimal("30.00")
