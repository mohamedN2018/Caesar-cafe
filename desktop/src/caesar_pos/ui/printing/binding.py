r"""
Pointing this terminal at its printers.

The branch defines its printers in the Web Admin, and they arrive on the config
stream. What the server cannot know is **where the cable is on THIS machine.**
`\\.\COM3` on the till by the door is a different device from `\\.\COM3` at the
back, so a branch-wide value is guaranteed to be wrong on some terminals.

That is what this dialog is for, and it is the whole scope of it: it does not
create, rename, or delete a printer — those are the manager's decisions and they
belong on the server where they reach every terminal at once. It only records
the local fact.

A binding is optional. Left empty, the branch's own `device_path` is used, which
is right in the common case where a cafe standardised on the same port
everywhere. The override exists for the case where they did not.

The list shows what each printer would resolve to right now, because "which
printer will this receipt actually come out of" is the only question anybody
opens this screen to answer.
"""

from __future__ import annotations

import logging

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

from ...local.db import Database
from ...printing import registry
from .. import palette as p

logger = logging.getLogger(__name__)

KIND_LABELS = {
    registry.RECEIPT: "فواتير",
    registry.KITCHEN: "مطبخ",
    registry.REPORT: "تقارير",
}

STYLESHEET = f"""
QLabel#Title   {{ font-size: 20px; font-weight: 700; }}
QLabel#Lead    {{ font-size: 13px; color: {p.INK_MUTED}; }}
QLabel#Empty   {{ font-size: 15px; color: {p.INK_MUTED}; }}
QLabel#Name    {{ font-size: 15px; font-weight: 600; color: {p.INK}; }}
QLabel#Kind    {{ font-size: 12px; color: {p.INK_MUTED}; }}
QLabel#Target  {{ font-size: 12px; color: {p.INK_FAINT}; }}
QFrame#Card    {{ background: {p.SURFACE}; border: 1px solid {p.BORDER_STRONG};
                  border-radius: 10px; }}
QLineEdit      {{ padding: 7px 10px; border: 1px solid {p.BORDER_STRONG};
                  border-radius: 8px; background: {p.SURFACE}; }}
QPushButton#Save {{ background: {p.BRAND_700}; color: {p.FG_ON_BRAND}; padding: 7px 14px; }}
"""


class PrinterBindingDialog(QDialog):
    """Emits `bound()` when a local path changes, so the shell can re-drain."""

    bound = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db

        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("طابعات هذا الجهاز")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(560, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        layout.addWidget(QLabel("طابعات هذا الجهاز", objectName="Title"))
        layout.addWidget(
            QLabel(
                "الطابعات بتتعرّف من لوحة الإدارة وبتوصل هنا لوحدها. اللي بيتظبط في الشاشة دي هو "
                "منفذ كل طابعة على الجهاز ده بالذات — لأن المنفذ صفة الجهاز مش صفة الفرع.\n"
                "سيبه فاضي علشان يستخدم المسار الافتراضي للفرع.",
                objectName="Lead",
            )
        )

        self.empty = QLabel(
            "مفيش طابعات معرّفة للفرع. الطباعة هتفضل على الطابعة المحلية زي ما هي.",
            objectName="Empty",
        )
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self.list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

        close = QPushButton("إغلاق")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignLeft)

        self.refresh()

    @property
    def printers(self) -> list[registry.Printer]:
        return registry.printers(self.db)

    def refresh(self) -> None:
        _clear(self.list)
        rows = self.printers
        self.empty.setVisible(not rows)

        for printer in rows:
            self.list.addWidget(self._card(printer))
        self.list.addStretch(1)

    def _card(self, printer: registry.Printer) -> QFrame:
        card = QFrame(objectName="Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(6)

        header = QHBoxLayout()
        name = QLabel(printer.name_ar, objectName="Name")
        header.addWidget(name)
        header.addStretch(1)

        kind = KIND_LABELS.get(printer.kind, printer.kind)
        if printer.is_default:
            kind += " · الافتراضية"
        header.addWidget(QLabel(kind, objectName="Kind"))
        box.addLayout(header)

        # The resolved target, not the configured one. What a receipt will
        # actually go to is the only thing worth showing here.
        target = QLabel(printer.target or "— بلا منفذ —", objectName="Target")
        target.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        box.addWidget(target)

        row = QHBoxLayout()
        field = QLineEdit()
        field.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        field.setPlaceholderText(r"مثال: \\.\COM3  أو  /dev/usb/lp0")
        field.setText(printer.device_path)
        row.addWidget(field, stretch=1)

        save = QPushButton("حفظ", objectName="Save")
        save.clicked.connect(lambda: self._bind(printer, field.text().strip()))
        row.addWidget(save)
        box.addLayout(row)

        if printer.connection == "NETWORK":
            # A network printer is reached by address, and an address is the
            # same from every till. Editing a port here would do nothing, and a
            # field that does nothing is worse than no field.
            field.setEnabled(False)
            save.setEnabled(False)
            field.setPlaceholderText("طابعة شبكة — العنوان واحد لكل الأجهزة.")

        return card

    def _bind(self, printer: registry.Printer, device_path: str) -> None:
        registry.bind(self.db, printer.id, device_path)
        logger.info("Printer bound locally", extra={"printer": printer.code})
        self.bound.emit()
        self.refresh()


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
