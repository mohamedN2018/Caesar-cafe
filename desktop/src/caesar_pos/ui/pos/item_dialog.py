"""
Choosing a size, the add-ons, and how many.

One dialog instead of three, because a cashier taking "two large lattes, one
with an extra shot, no sugar" should answer that in one pass rather than tapping
through a size chooser, then a modifier list, then a quantity stepper.

The running total is shown and updates as they tap. A customer standing at the
till asks "how much?" before the item is committed, and a cashier who has to add
it to find out has to void it if the answer is no.

**The price the dialog shows is the price the fold will compute.** Both sides run
the same arithmetic — base plus deltas, times quantity — and a test asserts they
agree. A dialog that quoted its own figure would be the one place the customer's
expectation and the bill diverge.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import palette as p
from .catalog import Tile

STYLESHEET = f"""
QLabel#ItemName  {{ font-size: 20px; font-weight: 700; }}
QLabel#GroupName {{ font-size: 13px; font-weight: 600; color: {p.INK_MUTED}; }}
QLabel#Total     {{ font-size: 30px; font-weight: 800; color: {p.BRAND_700}; }}
QLabel#Qty       {{ font-size: 26px; font-weight: 800; min-width: 56px; }}
QPushButton#Size       {{ background: {p.SURFACE_SUNKEN}; color: {p.INK}; }}
QPushButton#SizeActive {{ background: {p.BRAND_700}; color: {p.FG_ON_BRAND}; }}
QPushButton#Mod        {{ background: {p.SURFACE}; color: {p.INK};
                          border: 1px solid {p.BORDER_STRONG}; }}
QPushButton#ModActive  {{ background: {p.GOLD_500}; color: {p.FG_ON_GOLD};
                          border: 1px solid {p.GOLD_600}; }}
QPushButton#Step       {{ background: {p.SURFACE_SUNKEN}; color: {p.INK};
                          font-size: 26px; min-width: 60px; }}
"""


def line_total(unit_price: Decimal, modifiers: list[dict], quantity: Decimal) -> Decimal:
    """
    What this line will cost.

    The same expression the fold uses: base plus every delta, times quantity.
    Kept as a free function so `tests/test_item_dialog.py` can hold it against
    the fold's own result without constructing a widget.
    """
    unit = Decimal(unit_price)
    for modifier in modifiers:
        unit += Decimal(str(modifier.get("price_delta", "0")))
    return (unit * Decimal(quantity)).quantize(Decimal("0.01"))


class ItemDialog(QDialog):
    """Emits `confirmed(variant_id, quantity, modifiers, note)`."""

    confirmed = Signal(str, object, object, str)

    def __init__(
        self,
        tile: Tile,
        *,
        variants: list[Tile] | None = None,
        modifiers: list[dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle(tile.name_ar)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(480)

        self.variants = variants or [tile]
        self.available = modifiers or []
        self.chosen = self.variants[0]
        self.selected: list[dict] = []
        self.quantity = Decimal("1")

        # Built first, parented later. Choosing the opening size recomputes the
        # total, and that has to have somewhere to land.
        self.total = QLabel("", objectName="Total")
        self.size_buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        self.title = QLabel(self.chosen.name_ar, objectName="ItemName")
        layout.addWidget(self.title)

        if len(self.variants) > 1:
            layout.addWidget(QLabel("الحجم", objectName="GroupName"))
            self.size_row = QHBoxLayout()
            for variant in self.variants:
                button = QPushButton(f"{variant.name_ar}\n{variant.price}")
                button.setObjectName("Size")
                button.clicked.connect(lambda _=False, v=variant: self._choose_size(v))
                self.size_row.addWidget(button)
                self.size_buttons[variant.variant_id] = button
            layout.addLayout(self.size_row)
            self._choose_size(self.chosen)

        if self.available:
            layout.addWidget(QLabel("إضافات", objectName="GroupName"))
            holder = QWidget()
            grid = QVBoxLayout(holder)
            grid.setSpacing(6)

            self.mod_buttons: dict[str, QPushButton] = {}
            for modifier in self.available:
                delta = Decimal(str(modifier.get("price_delta", "0")))
                # A free add-on says nothing about price rather than "+0.00",
                # which reads like a charge somebody forgot to fill in.
                label = modifier["name_ar"] if delta == 0 else f"{modifier['name_ar']}  +{delta}"
                button = QPushButton(label)
                button.setObjectName("Mod")
                button.setCheckable(True)
                button.clicked.connect(lambda _=False, m=modifier: self._toggle(m))
                grid.addWidget(button)
                self.mod_buttons[modifier["id"]] = button

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(holder)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setMaximumHeight(200)
            layout.addWidget(scroll)

        self.note = QLineEdit()
        self.note.setPlaceholderText("ملاحظة للمطبخ — بدون سكر، سخن زيادة…")
        layout.addWidget(self.note)

        quantity_row = QHBoxLayout()
        minus = QPushButton("−", objectName="Step")
        minus.clicked.connect(lambda: self._step(-1))
        quantity_row.addWidget(minus)

        self.quantity_label = QLabel("1", objectName="Qty")
        self.quantity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quantity_row.addWidget(self.quantity_label)

        plus = QPushButton("+", objectName="Step")
        plus.clicked.connect(lambda: self._step(1))
        quantity_row.addWidget(plus)

        quantity_row.addStretch(1)
        quantity_row.addWidget(self.total)
        layout.addLayout(quantity_row)

        buttons = QHBoxLayout()
        cancel = QPushButton("إلغاء", objectName="Secondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.add_button = QPushButton("إضافة")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self._confirm)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)

        self._recompute()

    # ── choices ──────────────────────────────────────────────────────────────

    def _choose_size(self, variant: Tile) -> None:
        self.chosen = variant
        self.title.setText(variant.name_ar)
        for variant_id, button in self.size_buttons.items():
            button.setObjectName("SizeActive" if variant_id == variant.variant_id else "Size")
            button.style().polish(button)
        self._recompute()

    def _toggle(self, modifier: dict) -> None:
        existing = next((m for m in self.selected if m["id"] == modifier["id"]), None)
        if existing:
            self.selected.remove(existing)
        else:
            self.selected.append(
                {
                    "id": modifier["id"],
                    "name_ar": modifier["name_ar"],
                    "price_delta": str(modifier.get("price_delta", "0")),
                }
            )
        self._recompute()

    def _step(self, delta: int) -> None:
        # Never below one. A zero-quantity line is a line that should have been
        # cancelled, and letting it exist means a receipt with nothing on it.
        self.quantity = max(Decimal("1"), self.quantity + Decimal(delta))
        self.quantity_label.setText(str(self.quantity.normalize()))
        self._recompute()

    def _recompute(self) -> None:
        self.total.setText(f"{self.value} ج.م")

    @property
    def value(self) -> Decimal:
        return line_total(self.chosen.price, self.selected, self.quantity)

    def _confirm(self) -> None:
        self.confirmed.emit(
            self.chosen.variant_id,
            self.quantity,
            list(self.selected),
            self.note.text().strip(),
        )
        self.accept()
