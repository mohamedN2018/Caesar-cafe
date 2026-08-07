"""
Money arithmetic — the single authority on how an order total is computed.

This module is deliberately free of Django imports. It is pure Decimal logic so
the exact same algorithm can be vendored into the PySide6 Desktop client, and
both sides can be validated against the same golden-file fixture
(tests/fixtures/money_cases.json). If the Desktop and the server ever disagree
by one piaster, CI fails.

Rules (docs/02-data-model.md):
  * Decimal only. float never touches money.
  * ROUND_HALF_UP, applied once at each documented boundary — never mid-calculation.
  * The order of operations below is fixed and must not be reordered.

      line_gross     = round(unit_price_with_modifiers × quantity)
      line_discount  = round(line_gross × line_discount_pct / 100)
      line_net       = line_gross − line_discount
      order_net      = Σ line_net
      order_discount = round(order_net × order_discount_pct / 100)
      after_discount = order_net − order_discount
      service        = round(after_discount × service_pct / 100)
      tax            = f(after_discount, service, taxable_ratio, vat_pct, inclusive)
      grand_total    = after_discount + service + tax   (+ rounding adjustment)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "ZERO",
    "LineTotals",
    "OrderLine",
    "OrderTotals",
    "TaxRules",
    "compute_line",
    "compute_order",
    "quantize_money",
    "round_to_step",
]

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def quantize_money(value: Decimal) -> Decimal:
    """Round to 2 decimal places, half away from zero."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def round_to_step(value: Decimal, step: Decimal) -> Decimal:
    """
    Round a money value to the nearest multiple of `step`.

    Used for `finance.rounding_step` — a cafe that has abolished small coins can
    set 0.25 and have every total land on a quarter pound. A step of 0.01 (the
    default) is a no-op beyond normal money quantization.
    """
    if step <= ZERO:
        return quantize_money(value)
    steps = (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return quantize_money(steps * step)


@dataclass(frozen=True)
class TaxRules:
    """
    Resolved tax configuration for one order.

    Every field comes from the settings registry (docs/11-configuration.md).

    There are deliberately NO defaults here. A default would be a second source
    of truth for the VAT rate, and a service that constructed `TaxRules()` by
    mistake would silently charge 14% instead of the branch's configured rate.
    Requiring every field means that mistake cannot compile.
    """

    vat_percent: Decimal
    vat_enabled: bool
    vat_inclusive: bool
    service_percent: Decimal
    service_enabled: bool
    rounding_step: Decimal

    @property
    def effective_vat(self) -> Decimal:
        return self.vat_percent if self.vat_enabled else ZERO

    @property
    def effective_service(self) -> Decimal:
        return self.service_percent if self.service_enabled else ZERO


@dataclass(frozen=True)
class OrderLine:
    """One sellable line before totals are computed."""

    unit_price: Decimal
    quantity: Decimal = Decimal("1")
    discount_percent: Decimal = ZERO
    modifier_deltas: tuple[Decimal, ...] = field(default_factory=tuple)
    tax_exempt: bool = False

    @property
    def effective_unit_price(self) -> Decimal:
        return self.unit_price + sum(self.modifier_deltas, ZERO)


@dataclass(frozen=True)
class LineTotals:
    gross: Decimal
    discount: Decimal
    net: Decimal


@dataclass(frozen=True)
class OrderTotals:
    subtotal: Decimal
    """Sum of line nets, before any order-level discount."""
    discount_total: Decimal
    """Line discounts plus the order-level discount."""
    service_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    rounding_adjustment: Decimal
    """Difference introduced by `rounding_step`. Reported, never hidden."""
    lines: tuple[LineTotals, ...] = ()


def compute_line(line: OrderLine) -> LineTotals:
    gross = quantize_money(line.effective_unit_price * line.quantity)
    discount = quantize_money(gross * line.discount_percent / HUNDRED)
    return LineTotals(gross=gross, discount=discount, net=gross - discount)


def compute_order(
    lines: list[OrderLine] | tuple[OrderLine, ...],
    rules: TaxRules,
    order_discount_percent: Decimal = ZERO,
) -> OrderTotals:
    """
    Compute authoritative totals for an order.

    Tax on a mixed order is apportioned: when some lines are tax-exempt, VAT
    applies to the taxable share of the post-discount amount (service charge
    included, since service is itself taxable). Apportioning by value is the
    defensible reading and keeps a fully-exempt or fully-taxable order exact.
    """
    line_totals = tuple(compute_line(line) for line in lines)

    order_net = sum((lt.net for lt in line_totals), ZERO)
    line_discount_total = sum((lt.discount for lt in line_totals), ZERO)

    order_discount = quantize_money(order_net * order_discount_percent / HUNDRED)
    after_discount = order_net - order_discount

    service = quantize_money(after_discount * rules.effective_service / HUNDRED)

    taxable_net = sum(
        (lt.net for lt, line in zip(line_totals, lines, strict=True) if not line.tax_exempt),
        ZERO,
    )
    taxable_ratio = (taxable_net / order_net) if order_net else Decimal("0")

    tax = _compute_tax(
        base=after_discount + service,
        taxable_ratio=taxable_ratio,
        vat_percent=rules.effective_vat,
        inclusive=rules.vat_inclusive,
    )

    if rules.vat_inclusive:
        # Prices already contain VAT, so tax is disclosed rather than added.
        raw_total = after_discount + service
    else:
        raw_total = after_discount + service + tax

    grand_total = round_to_step(raw_total, rules.rounding_step)

    return OrderTotals(
        subtotal=quantize_money(order_net),
        discount_total=quantize_money(line_discount_total + order_discount),
        service_total=service,
        tax_total=tax,
        grand_total=grand_total,
        rounding_adjustment=quantize_money(grand_total - raw_total),
        lines=line_totals,
    )


def _compute_tax(
    *,
    base: Decimal,
    taxable_ratio: Decimal,
    vat_percent: Decimal,
    inclusive: bool,
) -> Decimal:
    if vat_percent <= ZERO or base <= ZERO or taxable_ratio <= ZERO:
        return ZERO

    taxable_base = quantize_money(base * taxable_ratio)

    if inclusive:
        # Extract the VAT already contained in the price.
        net_of_tax = taxable_base / (Decimal("1") + vat_percent / HUNDRED)
        return quantize_money(taxable_base - net_of_tax)

    return quantize_money(taxable_base * vat_percent / HUNDRED)
