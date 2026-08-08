"""
The cash drawer, locally.

A shift is the unit the money reconciles against: opening float in, sales and
payouts through, counted cash out, and the difference between what should be
there and what is. Without one there is no Z-report and no variance, which means
no way to notice that a drawer is short.

Three decisions, each of which is about the terminal being offline:

  * **The terminal computes its own Z-report.** A cashier at 1am with the
    internet down still has to count and go home. The server recomputes on
    receipt and *its* figure is the one that counts — and when the two differ,
    that difference is itself the finding, not an error to swallow.
  * **The shift id is minted here.** Every order and payment taken during the
    shift carries it, and those operations queue before the shift itself may have
    reached the server. A server-assigned id would leave them pointing at
    nothing (the server adopts the client's id for exactly this reason).
  * **One open shift per device.** A terminal that crashed mid-shift and opened
    a second on restart would end the day with two drawers to reconcile and no
    way to say which one was counted.

Only cash is counted. A card total that disagrees with the terminal's own log is
a payment-processor question, not a drawer question, and mixing them turns one
clear number into two vague ones.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ..local import outbox
from ..local.db import Database, transaction

logger = logging.getLogger(__name__)

OPEN = "OPEN"
CLOSED = "CLOSED"

#: Money leaving or entering the drawer for something that is not a sale.
PAY_IN = "PAY_IN"
PAY_OUT = "PAY_OUT"
DROP = "DROP"

MOVEMENT_LABELS = {
    PAY_IN: "إيداع",
    PAY_OUT: "صرف",
    DROP: "ترحيل للخزنة",
}

ZERO = Decimal("0.00")


class ShiftAlreadyOpen(RuntimeError):
    """This device already has a drawer open. Close it before opening another."""


class NoOpenShift(RuntimeError):
    """Nothing to close, and nothing to sell against."""


@dataclass(frozen=True)
class ZReport:
    """
    What the drawer should hold, and what the cashier says it holds.

    `variance` is the only number anybody looks at twice. It is reported signed:
    negative is short, which is the direction that matters.
    """

    shift_id: str
    opening_cash: Decimal
    cash_sales: Decimal
    non_cash_sales: Decimal
    pay_ins: Decimal
    pay_outs: Decimal
    expected_cash: Decimal
    counted_cash: Decimal | None
    order_count: int

    @property
    def variance(self) -> Decimal:
        if self.counted_cash is None:
            return ZERO
        return (self.counted_cash - self.expected_cash).quantize(Decimal("0.01"))

    @property
    def is_short(self) -> bool:
        return self.variance < 0


def current(db: Database) -> dict | None:
    row = db.one("SELECT * FROM l_shifts WHERE status = ? ORDER BY opened_at DESC", (OPEN,))
    return dict(row) if row else None


def require_open(db: Database) -> dict:
    shift = current(db)
    if shift is None:
        raise NoOpenShift("لا توجد وردية مفتوحة — افتح وردية قبل البيع.")
    return shift


def open_shift(
    db: Database,
    *,
    opening_cash: Decimal,
    user_id: str | None = None,
    shift_id: str | None = None,
) -> dict:
    """
    Start a drawer. The id is ours, and the server adopts it.

    Orders and payments queued during this shift reference this id, and they may
    reach the server before the shift operation does — the server's `shift_open`
    handler takes the client's id for precisely that reason.
    """
    if current(db) is not None:
        raise ShiftAlreadyOpen("توجد وردية مفتوحة بالفعل على هذا الجهاز.")

    opening_cash = Decimal(opening_cash).quantize(Decimal("0.01"))
    if opening_cash < 0:
        raise ValueError("العهدة الافتتاحية لا يمكن أن تكون سالبة")

    shift_id = shift_id or str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    with transaction(db.connection):
        db.insert(
            "l_shifts",
            {
                "id": shift_id,
                "user_id": user_id,
                "opening_cash": str(opening_cash),
                "status": OPEN,
                "opened_at": now,
            },
        )
        outbox.enqueue(
            db,
            entity_type="shift_open",
            entity_id=shift_id,
            payload={"shift_id": shift_id, "opening_cash": str(opening_cash)},
        )

    logger.info("Shift opened", extra={"shift": shift_id, "float": str(opening_cash)})
    return require_open(db)


def record_movement(
    db: Database,
    *,
    movement_type: str,
    amount: Decimal,
    reason: str,
) -> str:
    """
    Money in or out of the drawer for something that is not a sale.

    A reason is required and not defaulted. "Paid the milk man 200" reconciles;
    an unexplained 200 out of the drawer is the thing a variance report exists
    to surface, and letting it be recorded blank defeats the point.
    """
    if movement_type not in MOVEMENT_LABELS:
        raise ValueError(f"نوع حركة غير معروف: {movement_type}")
    if not reason.strip():
        raise ValueError("السبب مطلوب لكل حركة نقدية")

    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("المبلغ يجب أن يكون أكبر من صفر")

    shift = require_open(db)
    movement_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    with transaction(db.connection):
        db.insert(
            "l_cash_movements",
            {
                "id": movement_id,
                "shift_id": shift["id"],
                "movement_type": movement_type,
                "amount": str(amount),
                "reason": reason.strip(),
                "occurred_at": now,
            },
        )
        outbox.enqueue(
            db,
            entity_type="cash_movement",
            entity_id=movement_id,
            payload={
                "movement_id": movement_id,
                "shift_id": shift["id"],
                "movement_type": movement_type,
                "amount": str(amount),
                "reason": reason.strip(),
                "occurred_at": now,
            },
        )

    return movement_id


def z_report(db: Database, shift_id: str, *, counted_cash: Decimal | None = None) -> ZReport:
    """
    What this terminal believes the drawer holds.

    Computed from the local tables so it works with the network down. Cash and
    non-cash are separated because only cash is counted — a card total that
    disagrees with the log is a processor question, not a drawer one.
    """
    row = db.one("SELECT * FROM l_shifts WHERE id = ?", (shift_id,))
    if row is None:
        raise NoOpenShift("الوردية غير موجودة")

    opening = Decimal(row["opening_cash"])

    cash_sales = ZERO
    non_cash_sales = ZERO
    for payment in db.query(
        """
        SELECT p.amount, m.counts_as_cash
        FROM l_payments p
        LEFT JOIN m_payment_methods m ON m.id = p.method_id
        WHERE p.shift_id = ?
        """,
        (shift_id,),
    ):
        amount = Decimal(payment["amount"])
        # An unmirrored method counts as non-cash. Guessing "cash" would inflate
        # what the drawer should hold and manufacture a shortage.
        if payment["counts_as_cash"]:
            cash_sales += amount
        else:
            non_cash_sales += amount

    pay_ins = ZERO
    pay_outs = ZERO
    for movement in db.query(
        "SELECT movement_type, amount FROM l_cash_movements WHERE shift_id = ?", (shift_id,)
    ):
        amount = Decimal(movement["amount"])
        if movement["movement_type"] == PAY_IN:
            pay_ins += amount
        else:
            pay_outs += amount

    order_count = db.scalar(
        "SELECT COUNT(DISTINCT order_id) FROM l_payments WHERE shift_id = ?",
        (shift_id,),
        default=0,
    )

    stored_count = row["counted_cash"]
    if counted_cash is None and stored_count is not None:
        counted_cash = Decimal(stored_count)

    return ZReport(
        shift_id=shift_id,
        opening_cash=opening,
        cash_sales=cash_sales.quantize(Decimal("0.01")),
        non_cash_sales=non_cash_sales.quantize(Decimal("0.01")),
        pay_ins=pay_ins.quantize(Decimal("0.01")),
        pay_outs=pay_outs.quantize(Decimal("0.01")),
        expected_cash=(opening + cash_sales + pay_ins - pay_outs).quantize(Decimal("0.01")),
        counted_cash=(
            Decimal(counted_cash).quantize(Decimal("0.01")) if counted_cash is not None else None
        ),
        order_count=order_count,
    )


def close_shift(
    db: Database,
    *,
    counted_cash: Decimal,
    reason: str = "",
) -> ZReport:
    """
    Count down and close.

    The terminal's expected figure travels with the operation so the server can
    compare it against its own. A disagreement is not an error to hide — it means
    the two sides saw different sales, which is exactly what somebody needs to
    know before the cashier goes home.
    """
    shift = require_open(db)
    counted_cash = Decimal(counted_cash).quantize(Decimal("0.01"))
    if counted_cash < 0:
        raise ValueError("النقد المعدود لا يمكن أن يكون سالباً")

    report = z_report(db, shift["id"], counted_cash=counted_cash)
    now = datetime.now(UTC).isoformat()

    with transaction(db.connection):
        db.update(
            "l_shifts",
            {"status": CLOSED, "counted_cash": str(counted_cash), "closed_at": now},
            where="id = ?",
            params=(shift["id"],),
        )
        outbox.enqueue(
            db,
            entity_type="shift_close",
            entity_id=shift["id"],
            payload={
                "shift_id": shift["id"],
                "counted_cash": str(counted_cash),
                "client_expected_cash": str(report.expected_cash),
                "reason": reason,
            },
        )

    logger.info(
        "Shift closed",
        extra={
            "shift": shift["id"],
            "counted": str(counted_cash),
            "variance": str(report.variance),
        },
    )
    return report


def unsettled_orders(db: Database, shift_id: str) -> int:
    """
    Orders opened in this shift that still owe money.

    Closing over them would leave a table's bill attributed to a drawer nobody
    is standing at, so the close screen has to say so first.
    """
    return db.scalar(
        "SELECT COUNT(*) FROM l_orders WHERE shift_id = ? AND status NOT IN ('PAID', 'VOIDED')",
        (shift_id,),
        default=0,
    )
