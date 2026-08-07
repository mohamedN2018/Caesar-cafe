"""
Recipe costing and stock consumption.

Two jobs:
  * roll a recipe's ingredient costs up into the variant's `cost`, so gross
    profit is real rather than guessed
  * turn a sale into stock movements, server-side, under a row lock
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from apps.inventory.models import MovementType, StockLevel
from apps.inventory.services import apply_movement, convert

from .models import Recipe

logger = logging.getLogger(__name__)

MONEY_PLACES = Decimal("0.01")
COST_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class CostLine:
    item_code: str
    item_name: str
    quantity: Decimal
    unit_code: str
    unit_cost: Decimal
    line_cost: Decimal


@dataclass(frozen=True)
class RecipeCost:
    total: Decimal
    lines: tuple[CostLine, ...]
    missing_costs: tuple[str, ...]
    """Items with no recorded cost. The total understates reality by their value."""


def compute_cost(recipe: Recipe) -> RecipeCost:
    """
    What one portion costs, at today's weighted-average ingredient costs.

    Items that have never been received have no cost, so they are reported
    separately rather than silently contributing zero — a margin that looks
    great because an ingredient is missing is worse than no margin at all.
    """
    lines: list[CostLine] = []
    missing: list[str] = []
    total = Decimal("0")

    for line in recipe.lines.select_related("item", "item__base_unit", "unit").all():
        if line.is_optional:
            continue

        level = StockLevel.objects.filter(item=line.item).first()
        unit_cost = level.weighted_avg_cost if level else Decimal("0")
        if unit_cost == 0:
            missing.append(line.item.code)

        # Recipe quantities may be in any unit; costs are per base unit.
        base_quantity = convert(line.effective_quantity, line.unit, line.item.base_unit)
        line_cost = (base_quantity * unit_cost).quantize(COST_PLACES, rounding=ROUND_HALF_UP)
        total += line_cost

        lines.append(
            CostLine(
                item_code=line.item.code,
                item_name=line.item.name_ar,
                quantity=base_quantity,
                unit_code=line.item.base_unit.code,
                unit_cost=unit_cost,
                line_cost=line_cost,
            )
        )

    yield_quantity = recipe.yield_quantity or Decimal("1")
    per_portion = (total / yield_quantity).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)

    return RecipeCost(total=per_portion, lines=tuple(lines), missing_costs=tuple(missing))


@transaction.atomic
def refresh_variant_cost(recipe: Recipe) -> Decimal:
    """Recompute and store the variant's cost. Called whenever costs can move."""
    cost = compute_cost(recipe)
    variant = recipe.variant
    variant.cost = cost.total
    variant.save(update_fields=["cost", "updated_at"])
    return cost.total


def refresh_costs_for_item(item) -> int:
    """
    Re-cost every recipe using this ingredient.

    Called after a goods receipt: a new coffee price changes the margin on
    every drink containing coffee, and an owner should not have to ask.
    """
    recipes = (
        Recipe.objects.filter(lines__item=item, is_active=True).select_related("variant").distinct()
    )
    for recipe in recipes:
        refresh_variant_cost(recipe)
    return len(recipes)


@transaction.atomic
def consume_for_sale(
    *,
    variant,
    quantity: Decimal,
    ref=None,
    user=None,
    device_id=None,
    allow_negative: bool = True,
) -> list:
    """
    Deduct a sale's ingredients from stock.

    Runs SERVER-SIDE ONLY (commitment C6). The Desktop pushes what was sold; it
    never computes or reports a stock level, because reconciling two devices'
    independent deductions would be guesswork.
    """
    recipe = getattr(variant, "recipe", None)
    if recipe is None or not recipe.is_active:
        return []

    movements = []
    for line in recipe.lines.select_related("item", "item__base_unit", "unit").all():
        if line.is_optional:
            continue

        per_portion = convert(line.effective_quantity, line.unit, line.item.base_unit)
        consumed = per_portion * Decimal(quantity) / (recipe.yield_quantity or Decimal("1"))

        movements.append(
            apply_movement(
                item=line.item,
                quantity_delta=-consumed,
                movement_type=MovementType.SALE,
                ref=ref,
                user=user,
                device_id=device_id,
                reason=f"بيع {variant}",
                allow_negative=allow_negative,
            )
        )

    return movements


def consume_for_modifier(*, modifier, quantity: Decimal, ref=None, user=None, device_id=None):
    """An add-on that consumes stock, e.g. an extra espresso shot."""
    if modifier.inventory_item is None or modifier.quantity_consumed == 0:
        return None
    return apply_movement(
        item=modifier.inventory_item,
        quantity_delta=-(modifier.quantity_consumed * Decimal(quantity)),
        movement_type=MovementType.SALE,
        ref=ref,
        user=user,
        device_id=device_id,
        reason=f"إضافة {modifier.name_ar}",
    )
