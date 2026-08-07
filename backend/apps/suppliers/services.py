"""Supplier balances, kept as a ledger for the same reasons stock is."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import AppError

from .models import LedgerEntryType, Supplier, SupplierLedgerEntry


@transaction.atomic
def record_ledger_entry(
    *,
    supplier: Supplier,
    entry_type: str,
    amount: Decimal,
    ref=None,
    reference: str = "",
    notes: str = "",
    user=None,
    occurred_at=None,
) -> SupplierLedgerEntry:
    """
    Append to a supplier's statement and move their balance atomically.

    Row-locked for the same reason stock is: two receipts posted at once must
    not both read the old balance and write conflicting new ones.
    """
    locked = Supplier.objects.select_for_update().get(pk=supplier.pk)
    locked.current_balance = (locked.current_balance + Decimal(amount)).quantize(Decimal("0.01"))
    locked.save(update_fields=["current_balance", "updated_at"])

    return SupplierLedgerEntry.objects.create(
        supplier=locked,
        entry_type=entry_type,
        amount=Decimal(amount).quantize(Decimal("0.01")),
        balance_after=locked.current_balance,
        ref_type=ref.__class__.__name__ if ref is not None else "",
        ref_id=getattr(ref, "pk", None),
        reference=reference,
        notes=notes,
        user=user,
        occurred_at=occurred_at or timezone.now(),
    )


def record_payment(*, supplier: Supplier, amount: Decimal, reference: str = "", user=None):
    """Paying a supplier reduces what we owe, so it is a negative entry."""
    if amount <= 0:
        raise AppError("قيمة السداد يجب أن تكون أكبر من صفر", code="INVALID_AMOUNT")
    return record_ledger_entry(
        supplier=supplier,
        entry_type=LedgerEntryType.PAYMENT,
        amount=-Decimal(amount),
        reference=reference,
        user=user,
    )


def statement(supplier: Supplier, *, limit: int = 200) -> list[SupplierLedgerEntry]:
    return list(supplier.ledger.select_related("user")[:limit])


def reconcile(supplier: Supplier) -> Decimal:
    """
    Replay the ledger and compare against the stored balance.

    Returns the drift — zero means the projection is still trustworthy.

    Both sides are read fresh from the database. `record_ledger_entry` updates a
    row-locked copy, so any instance the caller happens to be holding may carry
    a stale balance — and a reconciliation that trusts a stale value is not
    reconciling anything.
    """
    stored = (
        Supplier.objects.filter(pk=supplier.pk).values_list("current_balance", flat=True).first()
    ) or Decimal("0")
    replayed = SupplierLedgerEntry.objects.filter(supplier_id=supplier.pk).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")
    return (stored - replayed).quantize(Decimal("0.01"))
