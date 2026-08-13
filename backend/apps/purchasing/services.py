"""
Receiving goods: the one operation that turns a purchase into stock.

Everything here happens in one transaction. A receipt that raised stock but
failed to bill the supplier — or the reverse — would be a partial financial
transaction, which docs/02 §49 forbids outright.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import AppError
from apps.inventory.models import MovementType
from apps.inventory.services import apply_movement, convert
from apps.recipes.services import refresh_costs_for_item
from apps.suppliers.models import LedgerEntryType
from apps.suppliers.services import record_ledger_entry

from .models import GoodsReceipt, POStatus, PurchaseOrder, PurchaseReturn

logger = logging.getLogger(__name__)


class AlreadyPosted(AppError):
    status_code = 409
    code = "ALREADY_POSTED"
    default_detail = "تم ترحيل هذا المستند بالفعل"


@transaction.atomic
def submit_purchase_order(po: PurchaseOrder, *, user=None) -> PurchaseOrder:
    """
    DRAFT → SUBMITTED. Deliberately does NOT touch stock.

    This is the rule the whole app exists for: ordering is an intention, and
    intentions do not appear on a shelf.
    """
    if po.status != POStatus.DRAFT:
        raise AppError(f"لا يمكن إرسال أمر شراء في حالة {po.status}", code="INVALID_PO_STATUS")
    if not po.lines.exists():
        raise AppError("أمر الشراء لا يحتوي على أصناف", code="EMPTY_PURCHASE_ORDER")

    po.status = POStatus.SUBMITTED
    po.submitted_at = timezone.now()
    po.updated_by = user
    po.save(update_fields=["status", "submitted_at", "updated_by", "updated_at"])

    from apps.audit import services as audit

    audit.record(
        "purchasing.po_approved",
        branch=po.branch,
        actor=user,
        obj=po,
        detail={
            "supplier": po.supplier.name if po.supplier_id else None,
            "lines": po.lines.count(),
        },
    )
    return po


@transaction.atomic
def post_receipt(receipt: GoodsReceipt, *, user=None) -> GoodsReceipt:
    """
    Post a goods receipt: stock in, supplier billed, costs refreshed.

    Receiving at the ACTUAL invoiced cost — not the ordered price — is what
    keeps weighted-average cost honest when a supplier raises prices between
    order and delivery.
    """
    if receipt.is_posted:
        raise AlreadyPosted()

    lines = list(receipt.lines.select_related("item", "item__base_unit", "unit").all())
    if not lines:
        raise AppError("سند الاستلام لا يحتوي على أصناف", code="EMPTY_RECEIPT")

    touched_items = set()

    for line in lines:
        # Suppliers quote in their own units (a 5kg sack); stock lives in base
        # units (grams). Convert before the ledger sees it.
        base_quantity = convert(line.quantity_received, line.unit, line.item.base_unit)
        if base_quantity <= 0:
            raise AppError(f"كمية غير صالحة للصنف {line.item.code}", code="INVALID_QUANTITY")

        # Unit cost must also be expressed per BASE unit, or a 5kg sack at
        # 100/kg would be recorded as 100 per gram.
        base_unit_cost = (line.unit_cost * line.quantity_received) / base_quantity

        apply_movement(
            item=line.item,
            quantity_delta=base_quantity,
            movement_type=MovementType.PURCHASE,
            unit_cost=base_unit_cost,
            ref=receipt,
            user=user,
            reason=f"استلام {receipt.grn_number}",
        )
        touched_items.add(line.item)

        if line.po_line is not None:
            line.po_line.quantity_received += line.quantity_received
            line.po_line.save(update_fields=["quantity_received", "updated_at"])

    record_ledger_entry(
        supplier=receipt.supplier,
        entry_type=LedgerEntryType.INVOICE,
        amount=receipt.grand_total,
        ref=receipt,
        reference=receipt.supplier_invoice_no,
        user=user,
        occurred_at=timezone.now(),
    )

    if receipt.purchase_order is not None:
        _advance_po_status(receipt.purchase_order)

    receipt.posted_at = timezone.now()
    receipt.received_by = receipt.received_by or user
    receipt.save(update_fields=["posted_at", "received_by", "updated_at"])

    # A new coffee price changes the margin on every drink containing coffee.
    for item in touched_items:
        refresh_costs_for_item(item)

    from apps.audit import services as audit

    audit.record(
        "purchasing.goods_received",
        branch=receipt.branch,
        actor=user,
        obj=receipt,
        object_label=receipt.grn_number,
        detail={
            "supplier": receipt.supplier.name,
            "lines": len(lines),
            "total": receipt.grand_total,
            "supplier_invoice": receipt.supplier_invoice_no,
        },
    )
    logger.info(
        "Goods receipt posted",
        extra={"grn": receipt.grn_number, "lines": len(lines), "total": str(receipt.grand_total)},
    )
    return receipt


def _advance_po_status(po: PurchaseOrder) -> None:
    po.refresh_from_db()
    if po.is_fully_received:
        po.status = POStatus.RECEIVED
    elif po.is_partially_received:
        po.status = POStatus.PARTIAL
    po.save(update_fields=["status", "updated_at"])


@transaction.atomic
def post_return(purchase_return: PurchaseReturn, *, user=None) -> PurchaseReturn:
    """Goods back to the supplier: stock out, balance down."""
    if purchase_return.posted_at is not None:
        raise AlreadyPosted()

    lines = list(purchase_return.lines.select_related("item", "item__base_unit", "unit").all())
    if not lines:
        raise AppError("المرتجع لا يحتوي على أصناف", code="EMPTY_RETURN")

    for line in lines:
        base_quantity = convert(line.quantity, line.unit, line.item.base_unit)
        apply_movement(
            item=line.item,
            quantity_delta=-base_quantity,
            movement_type=MovementType.RETURN,
            ref=purchase_return,
            user=user,
            reason=f"مرتجع {purchase_return.reference}: {purchase_return.reason}",
        )

    record_ledger_entry(
        supplier=purchase_return.supplier,
        entry_type=LedgerEntryType.RETURN,
        amount=-purchase_return.grand_total,
        ref=purchase_return,
        user=user,
        occurred_at=timezone.now(),
    )

    purchase_return.posted_at = timezone.now()
    purchase_return.save(update_fields=["posted_at", "updated_at"])
    return purchase_return


def reorder_suggestions(branch) -> list[dict]:
    """
    Items at or below their reorder level.

    Suggests the configured reorder quantity, or enough to reach the level
    again — whichever is larger, so a suggestion is never a no-op.
    """
    from apps.inventory.models import StockLevel

    suggestions = []
    levels = StockLevel.objects.select_related("item", "item__default_supplier").filter(
        item__branch=branch, item__is_active=True
    )

    for level in levels:
        item = level.item
        if item.reorder_level <= 0 or level.quantity_available > item.reorder_level:
            continue

        shortfall = item.reorder_level - level.quantity_available
        suggestions.append(
            {
                "item_id": str(item.id),
                "item_code": item.code,
                "item_name": item.name_ar,
                "on_hand": level.quantity_on_hand,
                "available": level.quantity_available,
                "reorder_level": item.reorder_level,
                "suggested_quantity": max(item.reorder_quantity, shortfall),
                "supplier": item.default_supplier.name if item.default_supplier else None,
                "supplier_id": str(item.default_supplier_id) if item.default_supplier_id else None,
            }
        )

    return sorted(suggestions, key=lambda s: s["available"])


def valuation(branch) -> dict:
    """Total value of stock on hand, at weighted-average cost."""
    from apps.inventory.models import StockLevel

    total = Decimal("0.00")
    by_type: dict[str, Decimal] = {}

    for level in StockLevel.objects.select_related("item").filter(
        item__branch=branch, item__is_active=True
    ):
        value = level.total_value
        total += value
        by_type[level.item.item_type] = by_type.get(level.item.item_type, Decimal("0")) + value

    return {"total": total, "by_type": by_type}
