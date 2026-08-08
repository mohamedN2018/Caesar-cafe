"""
Building the receipt.

The receipt is the one artefact of this system a customer takes home, and in
Egypt it is also a tax document. So it is built as a **list of lines** rather
than drawn directly: the same document renders to a printer, to a preview pane,
and to a reprint six months later, and all three are identical because there is
one builder.

The figures come from the folded order and are never recomputed here. A receipt
that added its own total would be the one place where the customer's copy and
the server's record disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..orders.fold import FoldedOrder

LINE_WIDTH = 42  # characters at the default font on 80mm paper
RULE = "-" * LINE_WIDTH


@dataclass(frozen=True)
class ReceiptHeader:
    branch_name: str = "كافيه القيصر"
    address: str = ""
    phone: str = ""
    tax_number: str = ""
    footer: str = "شكراً لزيارتكم"


@dataclass(frozen=True)
class Receipt:
    lines: list[str]
    kind: str = "RECEIPT"
    order_id: str = ""
    serial: str = ""
    meta: dict = field(default_factory=dict)

    def as_text(self) -> str:
        return "\n".join(self.lines)


def _row(left: str, right: str, width: int = LINE_WIDTH) -> str:
    """
    Two columns on one line.

    The label goes right and the figure left, because the document is RTL and the
    eye scans down the numbers. Padding is done here rather than by the printer,
    which has no notion of columns.
    """
    right = str(right)
    padding = max(1, width - len(left) - len(right))
    return f"{left}{' ' * padding}{right}"


def build(
    order: FoldedOrder,
    *,
    header: ReceiptHeader | None = None,
    serial: str = "",
    cashier: str = "",
    payments: list[dict] | None = None,
    issued_at: datetime | None = None,
    provisional: bool = False,
) -> Receipt:
    """
    One order → one receipt document.

    `provisional` marks a serial issued while the device was offline and had
    exhausted its invoice block (C9). It is printed on the slip so the customer's
    copy says what it is, rather than looking like an ordinary invoice number
    that will later belong to a different sale.
    """
    header = header or ReceiptHeader()
    issued_at = issued_at or datetime.now()
    totals = order.totals

    lines: list[str] = [header.branch_name]
    if header.address:
        lines.append(header.address)
    if header.phone:
        lines.append(header.phone)
    if header.tax_number:
        lines.append(f"الرقم الضريبي: {header.tax_number}")

    lines += [RULE, _row("فاتورة", serial or order.local_number)]
    if provisional:
        # Said plainly. A customer holding a slip that says "مؤقتة" and a
        # different number on the emailed copy needs to be able to connect them.
        lines.append("** فاتورة مؤقتة — سيصدر رقم نهائي عند الاتصال **")

    lines.append(_row("التاريخ", issued_at.strftime("%Y-%m-%d %H:%M")))
    if cashier:
        lines.append(_row("الكاشير", cashier))
    if order.table_id:
        lines.append(_row("الطاولة", order.table_id))

    lines.append(RULE)

    for item in order.active_items:
        quantity = f"{item.quantity:f}".rstrip("0").rstrip(".") or "1"
        lines.append(_row(f"{quantity}× {item.name_snapshot}", str(item.line_total)))

        for modifier in item.modifiers:
            lines.append(f"   + {modifier.get('name_ar', '')}")
        if item.note:
            lines.append(f"   ({item.note})")

    lines.append(RULE)
    lines.append(_row("المجموع", str(totals.subtotal)))

    # Zero rows are omitted. A receipt listing "الخصم 0.00" on every sale trains
    # the eye to skip the block where a real discount would appear.
    if totals.discount_total > Decimal("0"):
        lines.append(_row("الخصم", f"-{totals.discount_total}"))
    if totals.service_total > Decimal("0"):
        lines.append(_row("الخدمة", str(totals.service_total)))
    if totals.tax_total > Decimal("0"):
        lines.append(_row("ضريبة القيمة المضافة", str(totals.tax_total)))
    if totals.rounding_adjustment != Decimal("0"):
        lines.append(_row("تقريب", str(totals.rounding_adjustment)))

    lines += [RULE, _row("الإجمالي", str(totals.grand_total))]

    for payment in payments or []:
        lines.append(_row(payment.get("name_ar", "دفع"), str(payment.get("amount", ""))))
        if payment.get("change_given") and Decimal(str(payment["change_given"])) > 0:
            lines.append(_row("الباقي", str(payment["change_given"])))

    if order.balance_due > Decimal("0"):
        lines += [RULE, _row("المتبقي", str(order.balance_due))]

    lines += [RULE, header.footer]

    return Receipt(
        lines=lines,
        kind="RECEIPT",
        order_id=order.order_id,
        serial=serial or order.local_number,
        meta={"provisional": provisional, "grand_total": str(totals.grand_total)},
    )


def build_kitchen_ticket(
    order: FoldedOrder,
    *,
    station_name: str = "",
    items=None,
    issued_at: datetime | None = None,
) -> Receipt:
    """
    The kitchen slip — the offline fallback for the KDS.

    Deliberately holds NO prices. The kitchen needs to know what to make and for
    which table; a price on this slip is information leaking to a part of the
    operation that has no use for it and a reason to argue about it.
    """
    issued_at = issued_at or datetime.now()
    items = list(items if items is not None else order.unfired_items)

    lines = [
        station_name or "المطبخ",
        RULE,
        _row("طلب", order.local_number),
        _row("الوقت", issued_at.strftime("%H:%M")),
    ]
    if order.table_id:
        lines.append(_row("الطاولة", order.table_id))

    lines.append(RULE)

    for item in items:
        quantity = f"{item.quantity:f}".rstrip("0").rstrip(".") or "1"
        # Quantity FIRST and large: the number is what a cook reads across a
        # noisy kitchen, and a "2" mistaken for a "1" is a remake.
        lines.append(f"{quantity}×  {item.name_snapshot}")
        for modifier in item.modifiers:
            lines.append(f"    + {modifier.get('name_ar', '')}")
        if item.note:
            lines.append(f"    ** {item.note} **")

    lines.append(RULE)

    return Receipt(
        lines=lines,
        kind="KITCHEN",
        order_id=order.order_id,
        serial=order.local_number,
        meta={"station": station_name, "items": len(items)},
    )
