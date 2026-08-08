"""
Reading the catalog out of the mirror for the POS grid.

A thin query layer, kept separate from the widgets so the grid can be tested
without a database and the queries can be tested without Qt. It reads `m_*`
tables only — the UI never writes one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from ...local.db import Database


@dataclass(frozen=True)
class Category:
    id: str
    name_ar: str
    sort_order: int


@dataclass(frozen=True)
class Tile:
    """One button on the grid: a product and the variant a tap will add."""

    variant_id: str
    product_id: str
    name_ar: str
    price: Decimal
    category_id: str | None
    variant_count: int

    @property
    def needs_variant_choice(self) -> bool:
        """
        A product with one variant adds on a single tap; more than one opens a
        chooser. Making a cashier pick "regular" from a list of one is the kind
        of small tax that adds up over four hundred orders a day.
        """
        return self.variant_count > 1


def categories(db: Database) -> list[Category]:
    return [
        Category(id=row["id"], name_ar=row["name_ar"], sort_order=row["sort_order"])
        for row in db.query(
            "SELECT id, name_ar, sort_order FROM m_categories "
            "WHERE is_active = 1 ORDER BY sort_order, name_ar"
        )
    ]


def tiles(db: Database, *, category_id: str | None = None, search: str = "") -> list[Tile]:
    """
    The sellable products, with their default variant.

    Sorted by the admin's `sort_order` and not alphabetically: the grid layout is
    a decision the manager made about what staff reach for, and re-sorting it
    would throw away the muscle memory that makes a busy hour survivable.

    Category order comes first, so the "all" view groups the way the tabs do
    rather than interleaving cake and coffee wherever their numbers happen to
    collide.
    """
    where = ["p.is_active = 1", "p.is_sellable = 1"]
    params: list = []

    if category_id:
        where.append("p.category_id = ?")
        params.append(category_id)
    if search:
        where.append("(p.name_ar LIKE ? OR p.sku LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    rows = db.query(
        f"""
        SELECT p.id AS product_id, p.name_ar, p.category_id,
               v.id AS variant_id, v.price,
               (SELECT COUNT(*) FROM m_variants x
                 WHERE x.product_id = p.id AND x.is_active = 1) AS variant_count
        FROM m_products p
        JOIN m_variants v ON v.product_id = p.id AND v.is_active = 1
        LEFT JOIN m_categories c ON c.id = p.category_id
        WHERE {" AND ".join(where)}
          AND (v.is_default = 1 OR NOT EXISTS (
                SELECT 1 FROM m_variants d
                WHERE d.product_id = p.id AND d.is_default = 1 AND d.is_active = 1))
        ORDER BY COALESCE(c.sort_order, 9999), p.sort_order, p.name_ar
        """,  # noqa: S608 — `where` is built from fixed fragments; values are bound
        tuple(params),
    )

    return [
        Tile(
            variant_id=row["variant_id"],
            product_id=row["product_id"],
            name_ar=row["name_ar"],
            price=Decimal(row["price"]),
            category_id=row["category_id"],
            variant_count=row["variant_count"],
        )
        for row in rows
    ]


def variants_of(db: Database, product_id: str) -> list[Tile]:
    """The chooser's contents, for a product that has more than one size."""
    rows = db.query(
        """
        SELECT v.id AS variant_id, v.product_id, v.price, v.name_ar AS variant_name,
               p.name_ar AS product_name, p.category_id
        FROM m_variants v
        JOIN m_products p ON p.id = v.product_id
        WHERE v.product_id = ? AND v.is_active = 1
        ORDER BY v.sort_order, v.name_ar
        """,
        (product_id,),
    )

    return [
        Tile(
            variant_id=row["variant_id"],
            product_id=row["product_id"],
            name_ar=f"{row['product_name']} {row['variant_name'] or ''}".strip(),
            price=Decimal(row["price"]),
            category_id=row["category_id"],
            variant_count=1,
        )
        for row in rows
    ]


def payment_methods(db: Database) -> list[dict]:
    return [
        {
            "id": row["id"],
            "code": row["code"],
            "name_ar": row["name_ar"],
            "counts_as_cash": bool(row["counts_as_cash"]),
        }
        for row in db.query(
            "SELECT * FROM m_payment_methods WHERE is_active = 1 ORDER BY sort_order"
            if _has_column(db, "m_payment_methods", "sort_order")
            else "SELECT * FROM m_payment_methods WHERE is_active = 1 ORDER BY name_ar"
        )
    ]


def modifiers_for(db: Database, product_id: str) -> list[dict]:
    """
    Every active modifier, with its price delta.

    The delta is a string here and stays one until `money.py` sees it — a float
    on the way through is how an extra shot ends up costing 4.999999.
    """
    return [
        {
            "id": row["id"],
            "group_id": row["group_id"],
            "name_ar": row["name_ar"],
            "price_delta": row["price_delta"],
        }
        for row in db.query(
            "SELECT id, group_id, name_ar, price_delta FROM m_modifiers "
            "WHERE is_active = 1 ORDER BY sort_order"
            if _has_column(db, "m_modifiers", "sort_order")
            else "SELECT id, group_id, name_ar, price_delta FROM m_modifiers WHERE is_active = 1"
        )
    ]


def _has_column(db: Database, table: str, column: str) -> bool:
    return any(row["name"] == column for row in db.query(f"PRAGMA table_info({table})"))


def settings(db: Database) -> dict:
    """Every mirrored setting, decoded. The UI reads flags out of this."""
    return {
        row["key"]: json.loads(row["value"])
        for row in db.query("SELECT key, value FROM m_settings")
    }
