"""
CSV export.

CSV rather than XLSX because the destination is almost always an accountant's
spreadsheet or another system's import, and a format everything reads beats a
format that looks nicer in one program.

Two details that are not cosmetic:

  * A UTF-8 BOM is written first. Without it Excel on a Windows machine — which
    is every machine this cafe owns — renders Arabic column headers as mojibake,
    and the export is useless to the person who asked for it.

  * Money stays a string, exactly as the API returns it. Letting the CSV writer
    format a float would reintroduce the imprecision the entire system avoids.
"""

from __future__ import annotations

import csv
import io

BOM = "﻿"

#: Report key → (the list inside the payload, column order, Arabic headers).
LAYOUTS: dict[str, tuple[str, list[str], list[str]]] = {
    "sales/by-hour": (
        "hours",
        ["hour", "order_count", "net_sales"],
        ["الساعة", "عدد الطلبات", "صافي المبيعات"],
    ),
    "sales/by-category": (
        "categories",
        ["category", "quantity", "revenue", "profit", "share_percent"],
        ["القسم", "الكمية", "الإيراد", "الربح", "النسبة %"],
    ),
    "sales/by-payment-method": (
        "methods",
        ["method", "count", "amount", "counts_as_cash"],
        ["الطريقة", "العدد", "المبلغ", "نقدي"],
    ),
    "products/top": (
        "top",
        ["name", "category", "quantity", "revenue", "profit", "void_count"],
        ["الصنف", "القسم", "الكمية", "الإيراد", "الربح", "إلغاءات"],
    ),
    "products/profitability": (
        "products",
        ["name", "category", "quantity", "revenue", "cost", "profit", "margin_percent"],
        ["الصنف", "القسم", "الكمية", "الإيراد", "التكلفة", "الربح", "الهامش %"],
    ),
    "inventory/movements": (
        "movements",
        ["occurred_at", "item_code", "item", "type", "quantity_delta", "balance_after", "user"],
        ["التاريخ", "الكود", "الصنف", "النوع", "الحركة", "الرصيد بعدها", "المستخدم"],
    ),
    "inventory/waste": (
        "items",
        ["item", "quantity", "value", "events"],
        ["الصنف", "الكمية", "القيمة", "عدد المرات"],
    ),
    "inventory/variance": (
        "items",
        [
            "item_code",
            "item",
            "count_reference",
            "system_quantity",
            "counted_quantity",
            "variance",
            "value",
            "reason",
        ],
        ["الكود", "الصنف", "الجرد", "رصيد النظام", "المعدود", "الفرق", "القيمة", "السبب"],
    ),
    "purchases/summary": (
        "by_supplier",
        ["supplier", "receipts", "value"],
        ["المورد", "عدد الاستلامات", "القيمة"],
    ),
    "suppliers/balances": (
        "suppliers",
        ["name", "phone", "balance"],
        ["المورد", "الهاتف", "الرصيد"],
    ),
    "employees/sales": (
        "employees",
        ["name", "order_count", "net_sales", "average_ticket"],
        ["الموظف", "عدد الطلبات", "صافي المبيعات", "متوسط الفاتورة"],
    ),
    "employees/voids": (
        "employees",
        [
            "name",
            "order_count",
            "voided_orders",
            "voided_items",
            "void_rate_percent",
            "discount_rate_percent",
        ],
        ["الموظف", "الطلبات", "طلبات ملغاة", "أصناف ملغاة", "نسبة الإلغاء %", "نسبة الخصم %"],
    ),
    "shifts/variance": (
        "closes",
        ["closed_at", "user", "variance", "reason"],
        ["وقت الإغلاق", "الموظف", "الفرق", "السبب"],
    ),
}


def is_exportable(report: str) -> bool:
    return report in LAYOUTS


def to_csv(report: str, payload: dict) -> str:
    """
    Render one report's list section as CSV.

    Summary-shaped reports (a single object, not a list) fall through to a
    two-column key/value layout rather than being refused — an owner asking for
    the sales summary as a file should get a file.
    """
    buffer = io.StringIO()
    buffer.write(BOM)
    writer = csv.writer(buffer)

    if report in LAYOUTS:
        section, fields, headers = LAYOUTS[report]
        writer.writerow(headers)
        for row in payload.get(section, []):
            writer.writerow([row.get(field, "") for field in fields])
    else:
        writer.writerow(["البند", "القيمة"])
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                continue
            writer.writerow([key, value])

    return buffer.getvalue()


def filename(report: str, date_from, date_to) -> str:
    slug = report.replace("/", "-")
    return f"caesar-{slug}-{date_from}-{date_to}.csv"
