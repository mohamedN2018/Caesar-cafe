"""
The shell: one window, several boards, three background timers.

Everything up to now has been a piece — a fold, an outbox, a grid, a queue. This
is where they become an application, and the decisions worth stating are about
what runs on a timer and what does not.

**Three timers, three different reasons.**

  * *Sync* — every few seconds, because the outbox is what costs money to delay.
  * *Printing* — separately, because a jammed printer must not slow the till, and
    a busy sync must not delay a receipt. Coupling them means the customer waits
    for the network.
  * *Boards* — the floor map, the kitchen and the kids area re-read local state
    on their own beat. They read the mirror, so a tick is cheap and an outage
    only makes them show this terminal's own view — which the header says.

**Nothing blocks on the network.** Every timer callback either reads SQLite or
calls the engine, which is itself offline-tolerant. A tick that raises is logged
and swallowed: a POS that dies because one poll failed is worse than a POS that
is briefly stale.

**The header is permanent and never optimistic.** Who is on the till, the sync
state, and the print backlog if there is one. A terminal that has been queueing
since Tuesday says so on every screen, including the login screen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..api.client import ApiError, NetworkUnavailable
from ..kids import service as kids
from ..local import outbox
from ..local.db import Database
from ..orders import service
from ..printing import spooler
from ..security.session import Session
from ..shifts import service as shifts
from ..sync.engine import SyncEngine
from .floor.window import FloorWindow
from .history import HistoryDialog
from .kids.checkin import CheckInDialog
from .kids.window import KidsWindow
from .kitchen.window import KitchenWindow
from .pos.window import PosWindow
from .shift import CashMovementDialog, CloseShiftDialog, OpenShiftDialog, XReportDialog
from .sync import ConflictsDialog

logger = logging.getLogger(__name__)

#: Milliseconds. The outbox drains often because an unsent payment is money the
#: owner cannot see; the boards refresh less often because they read local state
#: that only this terminal changed.
SYNC_INTERVAL_MS = 5_000
PRINT_INTERVAL_MS = 3_000
BOARD_INTERVAL_MS = 10_000

STYLESHEET = """
QWidget#ShellHeader { background: #ffffff; border-bottom: 1px solid #e2e8f0; }
QLabel#ShellUser  { font-size: 15px; font-weight: 600; }
QLabel#ShellSync  { font-size: 14px; color: #475569; }
QLabel#ShellPrint { font-size: 14px; color: #b45309; font-weight: 600; }
QLabel#ShellShift { font-size: 14px; color: #475569; }
QPushButton#Shift { background: #e2e8f0; color: #0f172a; padding: 8px 14px; }
QPushButton#ShiftNeeded { background: #b45309; color: #ffffff; padding: 8px 14px; }
QPushButton#Conflicts { background: #b3261e; color: #ffffff; padding: 8px 14px; font-weight: 700; }
QPushButton#Tab       { background: #e2e8f0; color: #0f172a; padding: 10px 18px; }
QPushButton#TabActive { background: #1d4e89; color: #ffffff; padding: 10px 18px; }
QPushButton#Logout    { background: #e2e8f0; color: #0f172a; padding: 8px 14px; }
"""

#: Board → the permission that reveals its tab. A cook has no business on the
#: till, and the tab is hidden rather than disabled so the screen stays readable.
#: This is a UI decision only: the server authorises every operation again.
BOARD_PERMISSIONS = {
    "pos": "orders.create",
    "floor": "floor.view",
    "kitchen": "kitchen.view",
    "kids": "kids.view",
}

#: The board thinks in states; the API takes verbs (`/tickets/<id>/start/`).
#: Translated here, once, rather than teaching the board a URL vocabulary — and
#: an unmapped state is refused locally rather than sent as a guessed path.
TICKET_ACTIONS = {
    "ACCEPTED": "accept",
    "PREPARING": "start",
    "READY": "ready",
    "SERVED": "served",
    "CANCELLED": "cancel",
}

BOARD_LABELS = {
    "pos": "نقطة البيع",
    "floor": "الطاولات",
    "kitchen": "المطبخ",
    "kids": "صالة الأطفال",
}


class NotSyncedBoard(QWidget):
    """
    Stands in for a board whose data has not arrived.

    Carries the same small surface the shell calls on every board, so a missing
    catalog produces one honest sentence rather than a stream of AttributeErrors
    in the log and a blank rectangle on the screen.
    """

    def __init__(self, reason: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.reason = reason
        self.order_id = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        message = QLabel("لم تكتمل المزامنة بعد — لا يمكن فتح نقطة البيع.")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        layout.addWidget(message)

    def refresh_catalog(self) -> None:
        return

    def set_sync_label(self, text: str) -> None:
        return


class Shell(QWidget):
    """
    Emits `logout_requested`. The app owns what happens after.

    Constructed with the boards the session may actually see. A shell that built
    all four and hid three would still have four live timers refreshing screens
    nobody can open.
    """

    logout_requested = Signal()

    def __init__(
        self,
        db: Database,
        session: Session,
        engine: SyncEngine,
        *,
        printer=None,
        boards: list[str] | None = None,
        can_open_new_orders: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.session = session
        self.engine = engine
        self.printer = printer
        self.can_open_new_orders = can_open_new_orders

        self.setStyleSheet(STYLESHEET)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setWindowTitle("كافيه القيصر")
        self.setMinimumSize(1100, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.boards: dict[str, QWidget] = {}
        self.tabs: dict[str, QPushButton] = {}

        layout.addWidget(self._header())
        layout.addWidget(self.stack, stretch=1)

        for name in boards if boards is not None else available_boards(session):
            self._add_board(name)

        if self.boards:
            self.show_board(next(iter(self.boards)))

        self.refresh_status()
        self._start_timers()

    # ── construction ─────────────────────────────────────────────────────────

    def _header(self) -> QWidget:
        header = QWidget(objectName="ShellHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(10)

        self.tab_row = QHBoxLayout()
        holder = QWidget()
        holder.setLayout(self.tab_row)
        row.addWidget(holder)

        # "The receipt for the table that just left — can I have it again?" is
        # asked several times a shift, and the answer used to be no.
        self.history_button = QPushButton("طلبات اليوم", objectName="Shift")
        self.history_button.clicked.connect(self.show_history)
        row.addWidget(self.history_button)

        row.addStretch(1)

        # Only shown when there IS a backlog. A permanent "printer OK" is a label
        # staff stop reading, and then the one time it says otherwise nobody sees.
        self.print_label = QLabel("", objectName="ShellPrint")
        self.print_label.hide()
        row.addWidget(self.print_label)

        # The drawer, permanently. A terminal selling into no shift produces
        # sales that reconcile against nothing, and the cashier finds out at
        # close — which is the one moment it cannot be fixed.
        self.shift_label = QLabel("", objectName="ShellShift")
        row.addWidget(self.shift_label)

        self.shift_button = QPushButton("", objectName="Shift")
        self.shift_button.clicked.connect(self.toggle_shift)
        row.addWidget(self.shift_button)

        # Money in or out of the drawer for something that is not a sale. Shown
        # only while a drawer is open, because there is nothing to move into
        # otherwise.
        self.movement_button = QPushButton("حركة نقدية", objectName="Shift")
        self.movement_button.clicked.connect(self.cash_movement)
        row.addWidget(self.movement_button)

        # A read without closing. "How are we doing?" at eight in the evening
        # used to be answerable only by ending the shift.
        self.xreport_button = QPushButton("قراءة", objectName="Shift")
        self.xreport_button.clicked.connect(self.x_report)
        row.addWidget(self.xreport_button)

        # Conflicts are the one sync state that needs a person, and until this
        # button existed the header could say "⚠️ تعارض (٢)" with no way to find
        # out which two. Hidden when there are none.
        self.conflicts_button = QPushButton("", objectName="Conflicts")
        self.conflicts_button.clicked.connect(self.show_conflicts)
        self.conflicts_button.hide()
        row.addWidget(self.conflicts_button)

        self.sync_label = QLabel("", objectName="ShellSync")
        row.addWidget(self.sync_label)

        row.addWidget(QLabel(self.session.full_name_ar, objectName="ShellUser"))

        logout = QPushButton("خروج", objectName="Logout")
        logout.clicked.connect(self.logout_requested.emit)
        row.addWidget(logout)

        return header

    def _add_board(self, name: str) -> None:
        widget = self._build_board(name)
        if widget is None:
            return

        self.boards[name] = widget
        self.stack.addWidget(widget)

        tab = QPushButton(BOARD_LABELS[name], objectName="Tab")
        tab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tab.clicked.connect(lambda _=False, n=name: self.show_board(n))
        self.tab_row.addWidget(tab)
        self.tabs[name] = tab

    def _build_board(self, name: str) -> QWidget | None:
        if name == "pos":
            try:
                settings = service.settings_from_mirror(self.db)
            except RuntimeError as exc:
                # A terminal whose config has not pulled yet cannot price an
                # order, and `settings_from_mirror` rightly refuses to guess. The
                # shell still opens: the kitchen and the kids area work from
                # local state, and the till says what is missing instead of the
                # whole application failing to start.
                logger.error("Cannot build the till", extra={"reason": str(exc)})
                return NotSyncedBoard(str(exc))

            window = PosWindow(
                self.db,
                self.session,
                settings,
                can_open_new_orders=self.can_open_new_orders,
            )
            # The POS carries its own header for standalone use; inside the shell
            # the shell's header is the one that stays, so its logout is routed
            # here rather than leaving two different ways out.
            window.logout_requested.connect(self.logout_requested.emit)
            return window

        if name == "floor":
            window = FloorWindow(self.db)
            window.table_chosen.connect(self._open_table)
            return window

        if name == "kitchen":
            window = KitchenWindow(can_advance=self.session.can("kitchen.update_status"))
            window.advance.connect(self._advance_ticket)
            return window

        if name == "kids":
            window = KidsWindow(self.db)
            window.checkout_requested.connect(self._checkout_child)
            window.checkin_requested.connect(self._checkin_child)
            return window

        return None

    def _start_timers(self) -> None:
        self.sync_timer = _timer(self, SYNC_INTERVAL_MS, self.tick_sync)
        self.print_timer = _timer(self, PRINT_INTERVAL_MS, self.tick_print)
        self.board_timer = _timer(self, BOARD_INTERVAL_MS, self.tick_boards)

    # ── navigation ───────────────────────────────────────────────────────────

    def show_board(self, name: str) -> None:
        widget = self.boards.get(name)
        if widget is None:
            return

        self.stack.setCurrentWidget(widget)
        for board_name, tab in self.tabs.items():
            tab.setObjectName("TabActive" if board_name == name else "Tab")
            # Qt does not restyle on an objectName change on its own.
            tab.style().polish(tab)

        self.refresh_board(name)

    @property
    def current_board(self) -> str:
        for name, widget in self.boards.items():
            if widget is self.stack.currentWidget():
                return name
        return ""

    def _open_table(self, table_id: str, order_id) -> None:
        """A tap on the floor map lands on the till with that table's order."""
        pos = self.boards.get("pos")
        if pos is None:
            return

        if order_id:
            pos.order_id = order_id
            pos.panel.show_order(service.load(self.db, order_id))
        elif not self.can_open_new_orders:
            # A restricted licence still settles what is already on the tables;
            # seating a new one is the thing it refuses (C5).
            QMessageBox.warning(
                self,
                "الترخيص مقيّد",
                "لا يمكن فتح طلب جديد — يمكن إنهاء الطلبات المفتوحة فقط.",
            )
            return
        else:
            order = service.open_order(
                self.db, settings=service.settings_from_mirror(self.db), table_id=table_id
            )
            pos.order_id = order.order_id
            pos.panel.show_order(order)

        self.show_board("pos")

    def _advance_ticket(self, ticket_id: str, status: str) -> None:
        """
        The state change goes to the server, which owns kitchen state.

        Unlike an order, a ticket transition is NOT queued offline: two cooks on
        two terminals advancing the same ticket while disconnected would produce
        a conflict with no sensible resolution, and the board is only useful when
        it agrees with the other screens. Offline, the tap is refused out loud.
        """
        action = TICKET_ACTIONS.get(status)
        if action is None:
            logger.error("No API action for ticket state", extra={"state": status})
            return

        try:
            self.engine.client.post(f"/kitchen/tickets/{ticket_id}/{action}/")
        except NetworkUnavailable:
            QMessageBox.warning(
                self, "غير متصل", "شاشة المطبخ تحتاج اتصالاً بالخادم لتحديث حالة التذكرة."
            )
        except ApiError as exc:
            QMessageBox.warning(self, "تعذّر التنفيذ", str(exc))
        else:
            self.tick_boards()

    # ── the kids area ────────────────────────────────────────────────────────

    def _checkin_child(self) -> None:
        """
        Admit a child, online or not.

        This replaces a refusal. The shell used to insist check-in wait for the
        server, on the grounds that capacity is a safety limit — but refusing
        locally never prevented an over-admission, only the RECORD of one. A
        child in the room with no session is worse in every way: nobody knows
        the guardian, nothing is billed, the incident log is blank. The server
        was already written for this and flags a genuine over-admission as a
        conflict for a human.
        """
        zones = kids.areas(self.db)
        if not zones:
            QMessageBox.warning(self, "لم تتم المزامنة", "صالة الأطفال لم تصل لهذا الجهاز بعد.")
            return

        dialog = CheckInDialog(self.db, zones[0].id, parent=self)
        dialog.confirmed.connect(self._do_checkin)
        dialog.exec()

    def _do_checkin(self, payload: dict) -> None:
        try:
            kids.check_in(self.db, **payload)
        except (kids.AreaFull, kids.AreaUnknown, ValueError) as exc:
            QMessageBox.warning(self, "تعذّر الدخول", str(exc))
            return

        self.refresh_board("kids")
        self.refresh_status()

    def _checkout_child(self, session_id: str) -> None:
        """
        Recorded locally and queued; the CHARGE is the server's.

        The board shows a running figure from the vendored engine, but what the
        customer pays is computed once, on the server — one authority for money,
        as everywhere else. A terminal that could not release a child during an
        outage would leave a parent standing at a gate.
        """
        try:
            kids.check_out(self.db, session_id)
        except ValueError as exc:
            QMessageBox.warning(self, "تعذّر الخروج", str(exc))
            return

        self.refresh_board("kids")
        self.refresh_status()
        QMessageBox.information(
            self, "خروج", "تم تسجيل الخروج. قيمة الجلسة تُحتسب على الخادم وتظهر على الطلب."
        )

    # ── the drawer ───────────────────────────────────────────────────────────

    @property
    def shift(self) -> dict | None:
        return shifts.current(self.db)

    def toggle_shift(self) -> None:
        self.close_shift() if self.shift else self.open_shift()

    def open_shift(self) -> None:
        dialog = OpenShiftDialog(parent=self)
        dialog.confirmed.connect(self._do_open_shift)
        dialog.exec()

    def _do_open_shift(self, opening_cash) -> None:
        try:
            shifts.open_shift(self.db, opening_cash=opening_cash, user_id=self.session.user_id)
        except (shifts.ShiftAlreadyOpen, ValueError) as exc:
            QMessageBox.warning(self, "تعذّر فتح الوردية", str(exc))
            return
        self.refresh_status()

    def close_shift(self) -> None:
        shift = self.shift
        if shift is None:
            return

        dialog = CloseShiftDialog(
            shifts.z_report(self.db, shift["id"]),
            unsettled=shifts.unsettled_orders(self.db, shift["id"]),
            parent=self,
        )
        dialog.confirmed.connect(self._do_close_shift)
        dialog.exec()

    def _do_close_shift(self, counted_cash, reason: str) -> None:
        try:
            report = shifts.close_shift(self.db, counted_cash=counted_cash, reason=reason)
        except (shifts.NoOpenShift, ValueError) as exc:
            QMessageBox.warning(self, "تعذّر إغلاق الوردية", str(exc))
            return

        # Said plainly, and only the terminal's own figure. The server recomputes
        # on receipt and its number is the one that counts; promising this one is
        # final would be a promise an offline terminal cannot make.
        QMessageBox.information(
            self,
            "تم إغلاق الوردية",
            f"المتوقع {report.expected_cash} · المعدود {report.counted_cash}\n"
            f"الفرق {report.variance} ج.م\n\n"
            "سيعيد الخادم الحساب عند المزامنة، ورقمه هو المعتمد.",
        )
        self.refresh_status()

    def x_report(self) -> None:
        shift = self.shift
        if shift is None:
            QMessageBox.warning(self, "لا توجد وردية", "افتح وردية عشان تقدر تقرأ الدرج.")
            return
        XReportDialog(shifts.z_report(self.db, shift["id"]), parent=self).exec()

    def cash_movement(self) -> None:
        if self.shift is None:
            QMessageBox.warning(self, "لا توجد وردية", "افتح وردية قبل تسجيل حركة نقدية.")
            return

        dialog = CashMovementDialog(parent=self)
        dialog.confirmed.connect(self._do_cash_movement)
        dialog.exec()

    def _do_cash_movement(self, movement_type: str, amount, reason: str) -> None:
        try:
            shifts.record_movement(
                self.db, movement_type=movement_type, amount=amount, reason=reason
            )
        except (shifts.NoOpenShift, ValueError) as exc:
            QMessageBox.warning(self, "تعذّر التسجيل", str(exc))
            return
        self.refresh_status()

    # ── the timers ───────────────────────────────────────────────────────────

    def tick_sync(self) -> None:
        try:
            self.engine.tick()
        except Exception:
            logger.exception("Sync tick failed")
        self.refresh_status()

    def tick_print(self) -> None:
        if self.printer is None:
            return
        try:
            spooler.drain(self.db, self.printer)
        except Exception:
            logger.exception("Print drain failed")
        self.refresh_status()

    def tick_boards(self) -> None:
        """
        Only the visible board is refreshed.

        Rebuilding four boards on a ten-second timer is work nobody sees, and on
        the hardware these run on it is visible as jitter on the one board that
        is open.
        """
        self.refresh_board(self.current_board)

    def refresh_board(self, name: str) -> None:
        widget = self.boards.get(name)
        if widget is None:
            return

        try:
            if name == "floor":
                widget.refresh()
            elif name == "kids":
                widget.refresh()
            elif name == "kitchen":
                self._load_tickets(widget)
            elif name == "pos":
                widget.refresh_catalog()
        except Exception:
            logger.exception("Board refresh failed", extra={"board": name})

    def _load_tickets(self, board: KitchenWindow) -> None:
        try:
            data = self.engine.client.get("/kitchen/tickets/", params={"status": "OPEN"})
        except (NetworkUnavailable, ApiError):
            # The board keeps what it has and says the link is down. Blanking it
            # would lose tickets a cook is still working from.
            board.set_connection(live=False)
            return

        board.set_connection(live=True)
        board.show_tickets(data.get("results", data) if isinstance(data, dict) else data)

    def show_history(self) -> None:
        dialog = HistoryDialog(self.db, self.session, parent=self)
        dialog.reopen_requested.connect(self._reopen_order)
        dialog.exec()
        self.refresh_status()

    def _reopen_order(self, order_id: str) -> None:
        """An order still open goes back on the till, where it can be finished."""
        pos = self.boards.get("pos")
        if pos is None or not hasattr(pos, "panel"):
            return

        pos.order_id = order_id
        pos.panel.show_order(service.load(self.db, order_id))
        self.show_board("pos")

    def show_conflicts(self) -> None:
        dialog = ConflictsDialog(self.db, parent=self)
        dialog.resolved.connect(self.refresh_status)
        dialog.exec()
        self.refresh_status()

    def refresh_status(self) -> None:
        status = self.engine.status()
        self.sync_label.setText(str(status))

        shift = self.shift
        self.shift_label.setText(
            f"وردية · عهدة {shift['opening_cash']}" if shift else "لا توجد وردية"
        )
        self.shift_button.setText("إغلاق الوردية" if shift else "افتح وردية")
        # Amber until a drawer is open: selling into no shift produces sales
        # that reconcile against nothing.
        self.shift_button.setObjectName("Shift" if shift else "ShiftNeeded")
        self.shift_button.style().polish(self.shift_button)
        self.movement_button.setVisible(shift is not None)
        self.xreport_button.setVisible(shift is not None)

        # The count is the actionable half of the sync state. A cashier can do
        # nothing about "syncing"; a conflict is waiting on them specifically.
        unresolved = len(outbox.open_conflicts(self.db))
        self.conflicts_button.setVisible(bool(unresolved))
        if unresolved:
            self.conflicts_button.setText(f"⚠ {unresolved} تحتاج مراجعة")

        pos = self.boards.get("pos")
        if pos is not None:
            pos.set_sync_label(str(status))
            pos.shift_id = shift["id"] if shift else None

        counts = spooler.counts(self.db)
        backlog = counts["pending"] + counts["failed"]
        self.print_label.setVisible(bool(backlog))
        if backlog:
            self.print_label.setText(f"🖨 في انتظار الطباعة ({backlog})")

    def closeEvent(self, event) -> None:
        for timer in (self.sync_timer, self.print_timer, self.board_timer):
            timer.stop()
        super().closeEvent(event)


def available_boards(session: Session) -> list[str]:
    """
    Which tabs this person gets.

    A UI decision, and only a UI decision — §62's rule is that the desktop is
    never trusted for authorisation. Hiding the kitchen tab from a cashier is
    about a readable screen; the server refuses the operation regardless.
    """
    return [name for name, permission in BOARD_PERMISSIONS.items() if session.can(permission)]


def _timer(parent: QWidget, interval_ms: int, callback) -> QTimer:
    timer = QTimer(parent)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    timer.start()
    return timer


def started_at_label(session: Session, *, now: datetime | None = None) -> str:
    """How long this person has been on the till — a shift-change prompt."""
    minutes = int(((now or datetime.now(UTC)) - session.started_at).total_seconds() // 60)
    return f"{minutes // 60}:{minutes % 60:02d}"
