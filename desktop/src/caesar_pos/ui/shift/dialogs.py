"""
Opening, adjusting and closing the drawer.

Three small dialogs that carry most of the cash-handling discipline in the
product. What matters about them is what they refuse to make easy.

**The close screen shows the expected figure only after the count is entered.**
Showing it first turns counting into confirming: a cashier who can see "should
be 4,320" will find 4,320. The count comes first, then the comparison. This is
the single most important interaction decision in the whole cash path.

**A cash movement demands a reason.** "Paid the milk man 200" reconciles; an
unexplained 200 out of the drawer is precisely what the variance report exists
to surface, and a blank reason defeats it.

**A shortage is stated in words, not just coloured.** "ناقص ٤٥ ج.م" is what the
cashier has to explain, and a red number on a washed-out screen is not that.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...shifts import service as shifts

STYLESHEET = """
QLabel#Title      { font-size: 20px; font-weight: 700; }
QLabel#Hint       { font-size: 13px; color: #64748b; }
QLabel#Row        { font-size: 15px; }
QLabel#Expected   { font-size: 26px; font-weight: 800; color: #1d4e89; }
QLabel#VarianceOk    { font-size: 20px; font-weight: 700; color: #15803d; }
QLabel#VarianceShort { font-size: 20px; font-weight: 800; color: #b91c1c; }
QLabel#VarianceOver  { font-size: 20px; font-weight: 700; color: #b45309; }
QLabel#Warn       { font-size: 14px; font-weight: 600; color: #b45309; }
QLineEdit#Money   { font-size: 24px; padding: 12px; }
"""


def _money(text: str) -> Decimal | None:
    try:
        return Decimal(text.strip())
    except (InvalidOperation, ValueError):
        return None


class OpenShiftDialog(QDialog):
    """Emits `confirmed(opening_cash)`."""

    confirmed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("فتح وردية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        layout.addWidget(QLabel("فتح وردية جديدة", objectName="Title"))
        layout.addWidget(QLabel("اعدّ العهدة الافتتاحية في الدرج وأدخل قيمتها.", objectName="Hint"))

        self.amount = QLineEdit("0.00", objectName="Money")
        self.amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.amount.textChanged.connect(self._validate)
        layout.addWidget(self.amount)

        self.error = QLabel("", objectName="Warn")
        self.error.hide()
        layout.addWidget(self.error)

        row = QHBoxLayout()
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        self.confirm_button = QPushButton("فتح الوردية")
        self.confirm_button.setDefault(True)
        self.confirm_button.clicked.connect(self._confirm)
        row.addWidget(self.confirm_button)
        layout.addLayout(row)

        self.amount.setFocus()
        self.amount.selectAll()

    @property
    def value(self) -> Decimal | None:
        return _money(self.amount.text())

    def _validate(self) -> None:
        value = self.value
        valid = value is not None and value >= 0
        self.confirm_button.setEnabled(valid)
        self.error.setVisible(not valid)
        if not valid:
            self.error.setText("أدخل مبلغاً صحيحاً")

    def _confirm(self) -> None:
        if self.value is None or self.value < 0:
            return
        self.confirmed.emit(self.value)
        self.accept()


class CashMovementDialog(QDialog):
    """Emits `confirmed(movement_type, amount, reason)`."""

    confirmed = Signal(str, object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("حركة نقدية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(420)

        self.movement_type = shifts.PAY_OUT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        layout.addWidget(QLabel("حركة نقدية", objectName="Title"))

        self.type_buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        for code, label in shifts.MOVEMENT_LABELS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, c=code: self._choose(c))
            row.addWidget(button)
            self.type_buttons[code] = button
        layout.addLayout(row)
        self._choose(shifts.PAY_OUT)

        self.amount = QLineEdit("", objectName="Money")
        self.amount.setPlaceholderText("المبلغ")
        self.amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.amount.textChanged.connect(self._validate)
        layout.addWidget(self.amount)

        self.reason = QLineEdit("")
        self.reason.setPlaceholderText("السبب — مطلوب")
        self.reason.textChanged.connect(self._validate)
        layout.addWidget(self.reason)

        layout.addWidget(
            QLabel(
                "السبب مطلوب. مبلغ خارج من الدرج بلا تفسير هو بالضبط ما يظهر كعجز آخر اليوم.",
                objectName="Hint",
            )
        )

        buttons = QHBoxLayout()
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.confirm_button = QPushButton("تسجيل")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._confirm)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

    def _choose(self, code: str) -> None:
        self.movement_type = code
        for candidate, button in self.type_buttons.items():
            button.setChecked(candidate == code)

    def _validate(self) -> None:
        amount = _money(self.amount.text())
        self.confirm_button.setEnabled(
            amount is not None and amount > 0 and bool(self.reason.text().strip())
        )

    def _confirm(self) -> None:
        amount = _money(self.amount.text())
        if amount is None or amount <= 0 or not self.reason.text().strip():
            return
        self.confirmed.emit(self.movement_type, amount, self.reason.text().strip())
        self.accept()


class XReportDialog(QDialog):
    """
    A read of the drawer without closing it.

    Managers ask "how are we doing" at eight in the evening, and the only way to
    answer used to be to close the shift — which ends it. An X-report is the
    same figures with nothing written down.

    It shows the expected cash, unlike the CLOSE screen, and that is not an
    inconsistency: nobody is counting yet, so there is no count for the number
    to contaminate. Withholding it here would just make the manager close the
    till to find out, which is the outcome this exists to avoid.
    """

    def __init__(self, report: shifts.ZReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("قراءة الوردية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        layout.addWidget(QLabel("قراءة الوردية", objectName="Title"))
        layout.addWidget(
            QLabel("قراءة فقط — الوردية تفضل مفتوحة ومفيش حاجة بتتسجّل.", objectName="Hint")
        )

        for label, value in [
            ("العهدة الافتتاحية", report.opening_cash),
            ("مبيعات نقدية", report.cash_sales),
            ("مبيعات غير نقدية", report.non_cash_sales),
            ("إيداعات", report.pay_ins),
            ("مصروفات", report.pay_outs),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label, objectName="Row"))
            row.addStretch(1)
            row.addWidget(QLabel(f"{value} ج.م", objectName="Row"))
            layout.addLayout(row)

        layout.addWidget(QLabel(f"عدد الطلبات: {report.order_count}", objectName="Hint"))

        expected = QLabel(f"المتوقع في الدرج {report.expected_cash} ج.م", objectName="Expected")
        expected.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(expected)

        close = QPushButton("إغلاق")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class CloseShiftDialog(QDialog):
    """
    Count the drawer, then see the difference.

    Emits `confirmed(counted_cash, reason)`.
    """

    confirmed = Signal(object, str)

    def __init__(
        self,
        report: shifts.ZReport,
        *,
        unsettled: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)
        self.setWindowTitle("إغلاق الوردية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(460)

        self.report = report

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        layout.addWidget(QLabel("إغلاق الوردية", objectName="Title"))

        if unsettled:
            # Closing over an unpaid table attributes its bill to a drawer
            # nobody is standing at. Said out loud rather than blocked: the
            # cashier may genuinely be leaving a table to the next shift.
            layout.addWidget(QLabel(f"⚠ {unsettled} طلب مفتوح لم يُدفع بعد", objectName="Warn"))

        # Sales figures are shown; the EXPECTED CASH is not, until a count is
        # entered. Showing it first turns counting into confirming.
        for label, value in [
            ("العهدة الافتتاحية", report.opening_cash),
            ("مبيعات نقدية", report.cash_sales),
            ("مبيعات غير نقدية", report.non_cash_sales),
            ("إيداعات", report.pay_ins),
            ("مصروفات", report.pay_outs),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label, objectName="Row"))
            row.addStretch(1)
            row.addWidget(QLabel(f"{value} ج.م", objectName="Row"))
            layout.addLayout(row)

        layout.addWidget(QLabel(f"عدد الطلبات: {report.order_count}", objectName="Hint"))

        layout.addWidget(QLabel("اعدّ النقد في الدرج وأدخل الإجمالي:", objectName="Hint"))
        self.counted = QLineEdit("", objectName="Money")
        self.counted.setPlaceholderText("النقد المعدود")
        self.counted.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counted.textChanged.connect(self._recompute)
        layout.addWidget(self.counted)

        self.expected = QLabel("", objectName="Expected")
        self.expected.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.expected.hide()
        layout.addWidget(self.expected)

        self.variance = QLabel("", objectName="VarianceOk")
        self.variance.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.variance.hide()
        layout.addWidget(self.variance)

        self.reason = QLineEdit("")
        self.reason.setPlaceholderText("سبب الفرق")
        self.reason.textChanged.connect(self._recompute)
        self.reason.hide()
        layout.addWidget(self.reason)

        buttons = QHBoxLayout()
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.confirm_button = QPushButton("إغلاق الوردية")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._confirm)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        self.counted.setFocus()

    @property
    def counted_value(self) -> Decimal | None:
        return _money(self.counted.text())

    @property
    def variance_value(self) -> Decimal:
        counted = self.counted_value
        if counted is None:
            return Decimal("0.00")
        return (counted - self.report.expected_cash).quantize(Decimal("0.01"))

    def _recompute(self) -> None:
        counted = self.counted_value
        if counted is None or counted < 0:
            self.expected.hide()
            self.variance.hide()
            self.reason.hide()
            self.confirm_button.setEnabled(False)
            return

        # Revealed only now. Before the count, this number is an answer key.
        self.expected.setText(f"المتوقع {self.report.expected_cash} ج.م")
        self.expected.show()

        difference = self.variance_value
        if difference == 0:
            self.variance.setObjectName("VarianceOk")
            self.variance.setText("✔ مطابق")
        elif difference < 0:
            self.variance.setObjectName("VarianceShort")
            self.variance.setText(f"ناقص {abs(difference)} ج.م")
        else:
            self.variance.setObjectName("VarianceOver")
            self.variance.setText(f"زائد {difference} ج.م")

        self.variance.style().polish(self.variance)
        self.variance.show()

        # A difference has to be explained before the drawer closes on it.
        needs_reason = difference != 0
        self.reason.setVisible(needs_reason)
        self.confirm_button.setEnabled(not needs_reason or bool(self.reason.text().strip()))

    def _confirm(self) -> None:
        counted = self.counted_value
        if counted is None or counted < 0:
            return
        if self.variance_value != 0 and not self.reason.text().strip():
            return
        self.confirmed.emit(counted, self.reason.text().strip())
        self.accept()
