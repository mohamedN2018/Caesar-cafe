"""
Golden-file tests for money arithmetic.

This is the contract the Desktop client must also satisfy. The fixture is
loaded by both implementations; the assertions here are duplicated in the
Desktop test suite against the same JSON.

These tests need no database and no Django settings beyond import.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from apps.core.money import (
    OrderLine,
    TaxRules,
    compute_order,
    quantize_money,
    round_to_step,
)

FIXTURE = Path(__file__).parent / "fixtures" / "money_cases.json"


def _load() -> tuple[dict, list[dict]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["defaults"], data["cases"]


DEFAULTS, CASES = _load()


def _build_rules(overrides: dict) -> TaxRules:
    merged = {**DEFAULTS["rules"], **overrides}
    return TaxRules(
        vat_percent=Decimal(merged["vat_percent"]),
        vat_enabled=bool(merged["vat_enabled"]),
        vat_inclusive=bool(merged["vat_inclusive"]),
        service_percent=Decimal(merged["service_percent"]),
        service_enabled=bool(merged["service_enabled"]),
        rounding_step=Decimal(merged["rounding_step"]),
    )


def _build_line(raw: dict) -> OrderLine:
    return OrderLine(
        unit_price=Decimal(raw["unit_price"]),
        quantity=Decimal(raw.get("quantity", "1")),
        discount_percent=Decimal(raw.get("discount_percent", "0")),
        modifier_deltas=tuple(Decimal(d) for d in raw.get("modifier_deltas", [])),
        tax_exempt=bool(raw.get("tax_exempt", False)),
    )


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case: dict) -> None:
    rules = _build_rules(case.get("rules", {}))
    lines = [_build_line(raw) for raw in case["lines"]]
    order_discount = Decimal(case.get("order_discount_percent", DEFAULTS["order_discount_percent"]))

    totals = compute_order(lines, rules, order_discount)
    expected = case["expected"]

    mismatches = []
    for field_name, expected_raw in expected.items():
        actual = getattr(totals, field_name)
        want = Decimal(expected_raw)
        if actual != want:
            mismatches.append(f"  {field_name}: expected {want}, got {actual}")

    assert not mismatches, f"\n{case['id']} — {case['description']}\n" + "\n".join(mismatches)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_grand_total_reconciles(case: dict) -> None:
    """
    Independent cross-check: the components must sum to the total.

    This catches a whole class of bug the per-field assertions cannot — an
    implementation that produces the right grand total by a wrong route.
    """
    rules = _build_rules(case.get("rules", {}))
    lines = [_build_line(raw) for raw in case["lines"]]
    order_discount = Decimal(case.get("order_discount_percent", DEFAULTS["order_discount_percent"]))
    totals = compute_order(lines, rules, order_discount)

    line_discounts = sum((lt.discount for lt in totals.lines), Decimal("0.00"))
    order_level_discount = totals.discount_total - line_discounts
    after_discount = totals.subtotal - order_level_discount

    tax_component = Decimal("0.00") if rules.vat_inclusive else totals.tax_total
    rebuilt = after_discount + totals.service_total + tax_component + totals.rounding_adjustment

    assert quantize_money(rebuilt) == totals.grand_total, (
        f"{case['id']}: components sum to {quantize_money(rebuilt)} "
        f"but grand_total is {totals.grand_total}"
    )


class TestRoundToStep:
    @pytest.mark.parametrize(
        ("value", "step", "expected"),
        [
            ("117.76", "0.25", "117.75"),
            ("117.88", "0.25", "118.00"),
            ("117.87", "0.25", "117.75"),
            ("100.00", "0.25", "100.00"),
            ("10.125", "0.25", "10.25"),
            ("99.99", "0.05", "100.00"),
            ("99.99", "1.00", "100.00"),
            ("99.49", "1.00", "99.00"),
            ("12.34", "0.01", "12.34"),
        ],
    )
    def test_steps(self, value: str, step: str, expected: str) -> None:
        assert round_to_step(Decimal(value), Decimal(step)) == Decimal(expected)

    def test_zero_step_is_plain_quantize(self) -> None:
        assert round_to_step(Decimal("12.345"), Decimal("0")) == Decimal("12.35")


class TestQuantize:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0.125", "0.13"),  # HALF_UP, not HALF_EVEN
            ("0.135", "0.14"),
            ("0.124", "0.12"),
            ("-0.125", "-0.13"),  # away from zero
            ("13.9986", "14.00"),
        ],
    )
    def test_half_up(self, value: str, expected: str) -> None:
        assert quantize_money(Decimal(value)) == Decimal(expected)


def test_no_float_leaks_into_totals() -> None:
    """Every returned amount must be a Decimal — a float here is a defect."""
    totals = compute_order(
        [OrderLine(unit_price=Decimal("60.00"), quantity=Decimal("2"))],
        _build_rules({}),
    )
    for field_name in (
        "subtotal",
        "discount_total",
        "service_total",
        "tax_total",
        "grand_total",
        "rounding_adjustment",
    ):
        assert isinstance(getattr(totals, field_name), Decimal)


def test_fixture_has_no_duplicate_ids() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids in the golden file"
