"""
Everything that was deleted, and the way back.

The product almost never deletes. `SoftDeletableModel` sets `is_active = False`
and stamps `deactivated_at`, because a product that has ever been sold must not
be removable — deleting it would orphan historical line items and silently
rewrite last quarter's reports.

That is the right rule and it left a hole: **deactivated rows became invisible.**
A category switched off by accident was gone from every screen, still in the
database, with no way back that did not involve a shell. "Deleted" behaved like
deleted while promising it did not.

So this is not a new store or a new concept — it is a view of what was already
there. Fifteen models across nine apps, listed in one place, each restorable.

The registry is explicit rather than discovered by walking `__subclasses__`.
Discovery would silently pick up models that happen to inherit the mixin and have
no business being restored from a general admin screen, and the failure would be
an operator restoring something nobody meant to expose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.db.models import Model, QuerySet


@dataclass(frozen=True)
class Recyclable:
    """One model that can be deactivated and brought back."""

    app: str
    model: str
    #: Arabic, because this names a section on an Arabic screen.
    label: str
    #: The field a person recognises the row by.
    title_field: str = "name_ar"
    #: How to reach the owning organisation from this row, for tenant scoping.
    organization_path: str = "organization_id"

    @property
    def key(self) -> str:
        return f"{self.app}.{self.model}"

    def model_class(self) -> type[Model]:
        return apps.get_model(self.app, self.model)


#: What a manager may restore.
#:
#: Deliberately NOT every `SoftDeletableModel`. `Organization` is on the list of
#: soft-deletable models and is not here: restoring a whole tenant from a bin
#: beside "categories" is not an action anybody should reach by accident, and it
#: is not a mistake an operator makes and needs undone.
RECYCLABLE: tuple[Recyclable, ...] = (
    Recyclable("catalog", "Category", "الأقسام"),
    Recyclable("catalog", "Product", "المنتجات"),
    Recyclable(
        "catalog", "ProductVariant", "الأصناف", organization_path="product__organization_id"
    ),
    Recyclable("catalog", "Modifier", "الإضافات", organization_path="group__organization_id"),
    Recyclable("floor", "Area", "مناطق الصالة"),
    # A table belongs to an area, and the area holds the organisation. Checked
    # against the models rather than assumed — the first guess routed through
    # `area__branch` and 500ed the screen.
    Recyclable(
        "floor",
        "Table",
        "الطاولات",
        title_field="number",
        organization_path="area__organization_id",
    ),
    Recyclable("inventory", "InventoryItem", "أصناف المخزون"),
    # Missing until a payment-methods screen existed to switch one off.
    #
    # The endpoint hard-filtered `is_active=True` on read, so a deactivated tender
    # was invisible to every screen AND absent from here — retiring «فيزا» by
    # accident meant the branch could not take cards until somebody edited the
    # database. Both halves of that are fixed; this is the half that gets it back.
    Recyclable("payments", "PaymentMethod", "طرق الدفع"),
    Recyclable("kitchen", "Station", "محطات المطبخ"),
    Recyclable("printing", "Printer", "الطابعات"),
    Recyclable("suppliers", "Supplier", "الموردون", title_field="name"),
    Recyclable("kids", "PlayArea", "مناطق الأطفال"),
    Recyclable("kids", "PlayTariff", "تعريفات الأطفال", organization_path="area__organization_id"),
    Recyclable("hr", "WorkPattern", "أنماط الدوام"),
    Recyclable("organizations", "Branch", "الفروع"),
)

BY_KEY = {entry.key: entry for entry in RECYCLABLE}


def _manager(entry: Recyclable) -> QuerySet:
    """
    The manager that can SEE deactivated rows.

    Several of these models filter `is_active` out of their default manager, so
    asking `objects` for deleted rows returns nothing — the bin would render
    empty and look like good news. `all_objects` exists on the tenant-scoped base
    for exactly this, and `_base_manager` covers the rest.
    """
    model = entry.model_class()
    return getattr(model, "all_objects", None) or model._base_manager


def deleted_rows(entry: Recyclable, *, organization_id: Any) -> QuerySet:
    """Deactivated rows of one model, scoped to the caller's organisation."""
    return (
        _manager(entry)
        .filter(is_active=False, **{entry.organization_path: organization_id})
        .order_by("-deactivated_at")
    )


def describe(entry: Recyclable, row: Model) -> dict[str, Any]:
    """
    One row, as a person would recognise it.

    `title_field` varies because these models do not agree on what a name is: a
    table has a number, a supplier has one `name`, most have `name_ar`. Falling
    back to `str(row)` rather than to an id — an operator restoring something
    needs to know what it is, and a UUID tells them nothing.
    """
    title = getattr(row, entry.title_field, None) or str(row)
    return {
        "id": str(row.pk),
        "kind": entry.key,
        "kind_label": entry.label,
        "title": str(title),
        "deactivated_at": getattr(row, "deactivated_at", None),
    }


def restore(entry: Recyclable, row: Model) -> None:
    """
    Put it back.

    The mirror of `BranchScopedViewSet.perform_destroy`, and deliberately just as
    small: clearing the two fields it set. Anything more — cascading to children,
    reactivating a parent — would be this module inventing policy that the delete
    side never had.
    """
    row.is_active = True
    row.deactivated_at = None
    row.save(update_fields=["is_active", "deactivated_at", "updated_at"])
