"""
The door of the play area.

A parent with a restless child will not stand through a long form, so this asks
for four things and nothing else: the child, the guardian, a phone, and a tag.
Everything optional — the age, the medical note, the tariff — has a sensible
default and can be filled in later from the Web.

The medical field is on the form rather than behind "more details", because a
guardian mentions an allergy once, at the door, and if there is nowhere to put
it in that moment it does not get recorded at all.

Capacity is shown before anything is typed. Discovering the room is full after
filling a form is how a queue forms at a door.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...kids import service as kids
from ...local.db import Database
from .. import palette as p

STYLESHEET = f"""
QLabel#Title    {{ font-size: 20px; font-weight: 700; }}
QLabel#Capacity {{ font-size: 15px; font-weight: 700; }}
QLabel#Full     {{ font-size: 15px; font-weight: 800; color: {p.DANGER}; }}
QLabel#Hint     {{ font-size: 12px; color: {p.INK_MUTED}; }}
QLabel#Error    {{ font-size: 13px; font-weight: 600; color: {p.DANGER}; }}
QLineEdit#Tag   {{ font-size: 22px; font-weight: 800; }}
"""


class CheckInDialog(QDialog):
    """Emits `confirmed(payload)` — the shell performs it."""

    confirmed = Signal(dict)

    def __init__(self, db: Database, area_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.area_id = area_id

        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("دخول طفل")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(460)

        zone = kids.area(db, area_id)
        inside = kids.occupancy(db, area_id)
        self.is_full = bool(zone.max_capacity) and inside >= zone.max_capacity

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        layout.addWidget(QLabel("دخول طفل", objectName="Title"))

        # Said first, before a single field. Finding out the room is full after
        # filling a form is how a queue forms at a door.
        layout.addWidget(
            QLabel(
                f"الإشغال {inside} / {zone.max_capacity}",
                objectName="Full" if self.is_full else "Capacity",
            )
        )
        if self.is_full:
            layout.addWidget(QLabel("الصالة ممتلئة — لا يمكن إدخال طفل آخر.", objectName="Full"))

        self.child = QLineEdit()
        self.child.setPlaceholderText("اسم الطفل")
        self.child.textChanged.connect(self._validate)
        layout.addWidget(self.child)

        self.guardian = QLineEdit()
        self.guardian.setPlaceholderText("اسم ولي الأمر")
        self.guardian.textChanged.connect(self._validate)
        layout.addWidget(self.guardian)

        self.phone = QLineEdit()
        self.phone.setPlaceholderText("هاتف ولي الأمر")
        layout.addWidget(self.phone)

        self.tag = QLineEdit(objectName="Tag")
        self.tag.setPlaceholderText("رقم التاج")
        self.tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tag.textChanged.connect(self._validate)
        layout.addWidget(self.tag)

        # On the form, not behind "more". A guardian mentions an allergy once,
        # at the door; nowhere to put it means it is not recorded at all.
        self.medical = QLineEdit()
        self.medical.setPlaceholderText("ملاحظات طبية — حساسية، دواء…")
        layout.addWidget(self.medical)

        self.tariff = QComboBox()
        for option in kids.tariffs(db, area_id):
            self.tariff.addItem(option["name_ar"], option["id"])
        layout.addWidget(self.tariff)

        layout.addWidget(
            QLabel("يشتغل من غير إنترنت — الجلسة تتزامن أول ما الشبكة ترجع.", objectName="Hint")
        )

        self.error = QLabel("", objectName="Error")
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        cancel = QPushButton("إلغاء", objectName="Secondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.confirm_button = QPushButton("دخول")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._confirm)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        self.child.setFocus()

    def _validate(self) -> None:
        ready = bool(
            self.child.text().strip() and self.guardian.text().strip() and self.tag.text().strip()
        )
        self.confirm_button.setEnabled(ready and not self.is_full)

        # A tag already round another child's wrist makes matching a guess,
        # which is the one thing this subsystem exists to prevent.
        clash = bool(self.tag.text().strip()) and kids.tag_in_use(self.db, self.tag.text().strip())
        self.error.setVisible(clash)
        if clash:
            self.error.setText(f"التاج {self.tag.text().strip()} مستخدم مع طفل آخر")
            self.confirm_button.setEnabled(False)

    def _confirm(self) -> None:
        self.confirmed.emit(
            {
                "area_id": self.area_id,
                "child_name": self.child.text().strip(),
                "guardian_name": self.guardian.text().strip(),
                "guardian_phone": self.phone.text().strip(),
                "tag_number": self.tag.text().strip(),
                "medical_notes": self.medical.text().strip(),
                "tariff_id": self.tariff.currentData(),
            }
        )
        self.accept()
