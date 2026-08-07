"""
The stock ledger.

The governing rule: `StockLevel` is a projection, `StockMovement` is the truth.
These tests exist to prove that stays true under concurrency, and that a level
can never move without a movement explaining it.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connections

from apps.inventory import services
from apps.inventory.models import (
    CountLine,
    CountStatus,
    InventoryItem,
    MovementType,
    StockCount,
    StockLevel,
    StockMovement,
    Unit,
    UnitConversion,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def units(organization):
    kg = Unit.objects.create(organization=organization, code="KG", name_ar="كيلو")
    g = Unit.objects.create(organization=organization, code="G", name_ar="جرام")
    litre = Unit.objects.create(organization=organization, code="L", name_ar="لتر")
    ml = Unit.objects.create(organization=organization, code="ML", name_ar="مللي")
    UnitConversion.objects.create(from_unit=kg, to_unit=g, factor=Decimal("1000"))
    UnitConversion.objects.create(from_unit=litre, to_unit=ml, factor=Decimal("1000"))
    return {"KG": kg, "G": g, "L": litre, "ML": ml}


@pytest.fixture
def beans(organization, branch, units):
    return InventoryItem.objects.create(
        organization=organization,
        branch=branch,
        code="BEANS",
        name_ar="بن محمص",
        base_unit=units["G"],
        minimum_stock=Decimal("5000"),
    )


class TestUnitConversion:
    def test_direct_conversion(self, units) -> None:
        assert services.convert(Decimal("2"), units["KG"], units["G"]) == Decimal("2000.000")

    def test_inverse_conversion_needs_no_second_row(self, units) -> None:
        """Storing both directions invites them to disagree."""
        assert services.convert(Decimal("500"), units["G"], units["KG"]) == Decimal("0.500")

    def test_same_unit_is_identity(self, units) -> None:
        assert services.convert(Decimal("7.5"), units["G"], units["G"]) == Decimal("7.500")

    def test_a_missing_conversion_is_an_error_not_a_guess(self, units) -> None:
        with pytest.raises(services.UnitMismatch):
            services.convert(Decimal("1"), units["KG"], units["ML"])


class TestLedger:
    def test_a_movement_always_accompanies_a_level_change(self, beans) -> None:
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("1000"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.35"),
        )
        level = StockLevel.objects.get(item=beans)
        assert level.quantity_on_hand == Decimal("1000.000")
        assert StockMovement.objects.filter(item=beans).count() == 1

    def test_balance_after_is_snapshotted(self, beans) -> None:
        for delta in (Decimal("1000"), Decimal("-250"), Decimal("500")):
            services.apply_movement(
                item=beans, quantity_delta=delta, movement_type=MovementType.ADJUSTMENT
            )
        balances = list(
            StockMovement.objects.filter(item=beans)
            .order_by("created_at")
            .values_list("balance_after", flat=True)
        )
        assert balances == [Decimal("1000.000"), Decimal("750.000"), Decimal("1250.000")]

    def test_a_zero_movement_is_refused(self, beans) -> None:
        from apps.core.exceptions import AppError

        with pytest.raises(AppError):
            services.apply_movement(
                item=beans, quantity_delta=Decimal("0"), movement_type=MovementType.ADJUSTMENT
            )

    def test_negative_stock_can_be_blocked(self, beans) -> None:
        services.set_opening_balance(item=beans, quantity=Decimal("100"), unit_cost=Decimal("0.3"))
        with pytest.raises(services.InsufficientStock) as exc:
            services.apply_movement(
                item=beans,
                quantity_delta=Decimal("-500"),
                movement_type=MovementType.SALE,
                allow_negative=False,
            )
        assert exc.value.extra["available"] == "100.000"

    def test_negative_stock_is_allowed_by_default(self, beans) -> None:
        """A cafe that runs out mid-service must still be able to record the sale."""
        services.apply_movement(
            item=beans, quantity_delta=Decimal("-50"), movement_type=MovementType.SALE
        )
        assert StockLevel.objects.get(item=beans).quantity_on_hand == Decimal("-50.000")


class TestWeightedAverageCost:
    def test_hand_computed_example(self, beans) -> None:
        """
        1000g @ 0.30 = 300.00
        + 500g @ 0.45 = 225.00
        ------------------------
        1500g,  525.00  →  0.3500 per gram
        """
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("1000"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.30"),
        )
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("500"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.45"),
        )
        assert StockLevel.objects.get(item=beans).weighted_avg_cost == Decimal("0.3500")

    def test_consuming_stock_does_not_change_the_average(self, beans) -> None:
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("1000"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.30"),
        )
        services.apply_movement(
            item=beans, quantity_delta=Decimal("-400"), movement_type=MovementType.SALE
        )
        assert StockLevel.objects.get(item=beans).weighted_avg_cost == Decimal("0.3000")

    def test_a_sale_is_valued_at_the_current_average(self, beans) -> None:
        """This is what makes COGS meaningful."""
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("1000"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.30"),
        )
        result = services.apply_movement(
            item=beans, quantity_delta=Decimal("-100"), movement_type=MovementType.SALE
        )
        assert result.movement.unit_cost == Decimal("0.3000")
        assert result.movement.value_delta == Decimal("-30.00")

    def test_first_receipt_sets_the_average(self, beans) -> None:
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("200"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.55"),
        )
        assert StockLevel.objects.get(item=beans).weighted_avg_cost == Decimal("0.5500")

    def test_receiving_into_negative_stock_takes_the_new_cost(self, beans) -> None:
        """A negative balance would make a weighted average meaningless."""
        services.apply_movement(
            item=beans, quantity_delta=Decimal("-100"), movement_type=MovementType.SALE
        )
        services.apply_movement(
            item=beans,
            quantity_delta=Decimal("500"),
            movement_type=MovementType.PURCHASE,
            unit_cost=Decimal("0.40"),
        )
        assert StockLevel.objects.get(item=beans).weighted_avg_cost == Decimal("0.4000")


class TestOperations:
    def test_waste_reduces_stock_and_records_the_reason(self, beans) -> None:
        services.set_opening_balance(item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.3"))
        result = services.record_waste(item=beans, quantity=Decimal("120"), reason="انسكاب")

        assert result.level.quantity_on_hand == Decimal("880.000")
        assert result.movement.movement_type == MovementType.WASTE
        assert result.movement.reason == "انسكاب"

    def test_adjustment_records_the_difference_not_the_target(self, beans) -> None:
        """An adjustment leaving no movement would be an unexplained change."""
        services.set_opening_balance(item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.3"))
        result = services.adjust(item=beans, new_quantity=Decimal("950"), reason="جرد")

        assert result.movement.quantity_delta == Decimal("-50.000")
        assert result.level.quantity_on_hand == Decimal("950.000")

    def test_adjusting_to_the_same_value_is_a_no_op(self, beans) -> None:
        services.set_opening_balance(item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.3"))
        assert services.adjust(item=beans, new_quantity=Decimal("1000"), reason="x") is None
        assert StockMovement.objects.filter(item=beans).count() == 1


class TestStockCount:
    def test_posting_turns_variances_into_movements(self, organization, branch, beans) -> None:
        services.set_opening_balance(item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.3"))

        count = StockCount.objects.create(
            organization=organization, branch=branch, reference="C-001"
        )
        CountLine.objects.create(
            count=count,
            item=beans,
            system_quantity=Decimal("1000"),
            counted_quantity=Decimal("940"),
            reason="فرق جرد",
        )

        services.post_count(count)
        count.refresh_from_db()

        assert count.status == CountStatus.POSTED
        assert StockLevel.objects.get(item=beans).quantity_on_hand == Decimal("940.000")
        assert StockMovement.objects.filter(item=beans, movement_type=MovementType.COUNT).exists()

    def test_posting_twice_is_refused(self, organization, branch, beans) -> None:
        from apps.core.exceptions import AppError

        count = StockCount.objects.create(
            organization=organization, branch=branch, reference="C-002"
        )
        services.post_count(count)
        with pytest.raises(AppError):
            services.post_count(count)

    def test_uncounted_lines_are_skipped(self, organization, branch, beans) -> None:
        count = StockCount.objects.create(
            organization=organization, branch=branch, reference="C-003"
        )
        CountLine.objects.create(
            count=count, item=beans, system_quantity=Decimal("100"), counted_quantity=None
        )
        services.post_count(count)
        assert not StockMovement.objects.filter(item=beans).exists()


class TestReconciliation:
    def test_a_clean_ledger_reports_no_drift(self, branch, beans) -> None:
        services.set_opening_balance(item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.3"))
        services.record_waste(item=beans, quantity=Decimal("50"), reason="x")
        assert services.reconcile(branch) == []

    def test_drift_is_detected(self, branch, beans) -> None:
        """
        Simulates a code path that bypassed apply_movement — the exact bug this
        reconciliation exists to catch.
        """
        services.set_opening_balance(item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.3"))
        StockLevel.objects.filter(item=beans).update(quantity_on_hand=Decimal("1234"))

        drifts = services.reconcile(branch)
        assert len(drifts) == 1
        assert drifts[0].item_code == "BEANS"
        assert drifts[0].difference == Decimal("234.000")

    def test_low_stock_counts_reserved_as_gone(self, branch, beans) -> None:
        services.set_opening_balance(item=beans, quantity=Decimal("6000"), unit_cost=Decimal("0.3"))
        assert services.low_stock(branch) == []

        StockLevel.objects.filter(item=beans).update(quantity_reserved=Decimal("2000"))
        assert len(services.low_stock(branch)) == 1


@pytest.mark.django_db(transaction=True)
class TestConcurrentDeduction:
    """
    Phase 4 exit criterion.

    Without `select_for_update`, two simultaneous cappuccino sales both read
    500g of beans and both write 482g — losing 18g silently, every time it
    races. Fifty parallel sales make that impossible to miss.
    """

    def test_fifty_parallel_sales_deduct_exactly(self, organization, branch) -> None:
        unit = Unit.objects.create(organization=organization, code="G", name_ar="جرام")
        item = InventoryItem.objects.create(
            organization=organization,
            branch=branch,
            code="BEANS",
            name_ar="بن",
            base_unit=unit,
        )
        services.set_opening_balance(
            item=item, quantity=Decimal("10000"), unit_cost=Decimal("0.30")
        )

        errors: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(50)

        def sell() -> None:
            try:
                start.wait(timeout=20)
                services.apply_movement(
                    item=item,
                    quantity_delta=Decimal("-18"),
                    movement_type=MovementType.SALE,
                )
            except Exception as exc:  # surfaced in the assertion below
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=sell) for _ in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, f"unexpected failures: {errors[:3]}"

        level = StockLevel.objects.get(item=item)
        expected = Decimal("10000") - (Decimal("18") * 50)
        assert level.quantity_on_hand == expected, (
            f"lost stock to a race: expected {expected}, got {level.quantity_on_hand}"
        )
        assert (
            StockMovement.objects.filter(item=item, movement_type=MovementType.SALE).count() == 50
        )
        assert services.reconcile(branch) == []
