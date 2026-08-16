"""
A whole cafe, populated, so every screen has something true on it.

This is not fixtures. It builds a plausible fortnight of trading through the
**real services** — `open_order`, `apply_events`, `take_payment`, `post_receipt`,
`check_in` — so the data it leaves behind obeys every rule the product enforces.
Rows inserted straight into the tables would give screens something to render
and would prove nothing; totals would not tie, stock would not move, and the
first real sale would look different from all of them.

Two consequences worth stating:

  * **It refuses to run against a database that has orders**, unless `--force`.
    Seed data mixed into a real cafe's ledger is unrecoverable without knowing
    which rows were which, and by the time anybody notices, a month has closed.
  * **It is deterministic.** A fixed seed means the demo you show on Tuesday is
    the demo you debugged on Monday, and a screenshot in a bug report can be
    reproduced exactly.

    python manage.py seed_demo --days 14
"""

from __future__ import annotations

import random
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.authz.models import RoleAssignment
from apps.authz.services import ensure_system_roles
from apps.catalog.models import (
    Category,
    Modifier,
    ModifierGroup,
    Product,
    ProductVariant,
    VariantChannelPrice,
)
from apps.floor.models import Area, Table, TableSession, TableShape, TableStatus
from apps.inventory.models import InventoryItem, ItemType, Unit, UnitConversion
from apps.kids.models import PlayArea, PlayTariff, TariffMode
from apps.kitchen.models import Station
from apps.licensing import services as licensing_services
from apps.licensing.models import Device, InvoiceBlock, License, LicenseEvent, LicenseType
from apps.orders.models import EventType, Order, OrderType
from apps.organizations.models import Branch, Organization
from apps.payments.models import PaymentMethod
from apps.printing.models import Printer
from apps.purchasing.models import GoodsReceipt, GRLine, PurchaseOrder
from apps.recipes.models import Recipe, RecipeLine
from apps.shifts.models import Shift
from apps.suppliers.models import Supplier, SupplierLedgerEntry

#: (code, Arabic name, English name, phone, address, trading weight)
#:
#: One branch, by request. The list stays a list rather than collapsing back to a
#: single hardcoded branch, because the code around it — the per-branch build loop,
#: the licence per branch, the per-branch summary — is correct for one or for many,
#: and a list of one is not a workaround. Adding a second branch is appending a row
#: here; going back to a hardcoded single branch would mean rewriting the loop,
#: and then rewriting it again the next time.
#:
#: `weight` scales how busy the branch is relative to the main one. With one branch
#: it is 1.0 and does nothing; it is what a second branch would use to avoid
#: trading identically to the first, which would make a branch comparison look
#: correct whichever column it sorted by.
BRANCHES = [
    (
        "MB",
        "الفرع الرئيسي",
        "Main Branch",
        "0132600000",
        "شارع الصلاحة، آخر شارع قاعة الدار البيضاء، بحري شبين القناطر",
        Decimal("1.0"),
    ),
]

#: Fixed, so Tuesday's demo is Monday's demo.
SEED = 20260808

DEMO_PASSWORD = "caesar-demo-2026"  # noqa: S105 — a demo credential, printed on stdout

#: (email, name, role, PIN). The PIN is per PERSON, never per role: the Desktop
#: identifies a cashier by which stored hash their PIN matches, so two people
#: sharing 3333 makes "who took this payment" a coin toss — and that is the one
#: question the audit trail exists to answer.
STAFF = [
    ("owner@caesar.test", "محمد القيصر", "SUPER_ADMIN", "1111"),
    ("manager@caesar.test", "أحمد عبد الرحمن", "BRANCH_MANAGER", "2222"),
    ("cashier@caesar.test", "منى سعيد", "CASHIER", "3333"),
    ("cashier2@caesar.test", "كريم فؤاد", "CASHIER", "3344"),
    ("waiter@caesar.test", "يوسف طارق", "WAITER", "4444"),
    ("waiter2@caesar.test", "عمر حسن", "WAITER", "4455"),
    ("kitchen@caesar.test", "سيد الشيف", "KITCHEN", "5555"),
    ("kids@caesar.test", "سارة إبراهيم", "KIDS_STAFF", "6666"),
    ("store@caesar.test", "حسام أمين", "INVENTORY_MANAGER", "7777"),
    ("accountant@caesar.test", "نهى مصطفى", "ACCOUNTANT", "8888"),
]

#: (code, name, target prep minutes, auto-accept)
#:
#: The targets differ on purpose and the difference is the whole point: an
#: espresso is late at four minutes and a grilled order is not late at ten. One
#: global target would leave the kitchen permanently red and the bar permanently
#: green, and at that point nobody reads the colour at all.
#:
#: Auto-accept is on for the two bars, off for the two that cook. A barista
#: standing at the machine sees the ticket appear and starts; there is nothing
#: for a tap to add. A kitchen accepting a ticket is a cook saying "mine", which
#: is real information on a line with three people on it.
STATIONS = [
    ("COFFEE", "بار القهوة", 4, True),
    ("COLD", "بار العصائر والبارد", 5, True),
    ("HOT", "المطبخ الساخن", 12, False),
    ("DESSERT", "بار الحلويات", 8, False),
]

#: (category, colour, [(sku, name, station, [(variant, price)])])
#:
#: **The station on every line is the routing rule, not a label.** It decides
#: which bar the ticket prints at when the order is fired, so a dessert filed
#: under the coffee bar is a waffle nobody is making while the barista wonders
#: why there is a cake on their slip. The categories are how a cashier finds an
#: item; the stations are how the kitchen finds out about it, and the two are
#: deliberately not the same axis — a caesar salad lives under food and is made
#: at the cold bar.
MENU = [
    (
        "مشروبات ساخنة",
        "#7b1e28",
        [
            ("ESP", "إسبريسو", "COFFEE", [("سنجل", "35.00"), ("دوبل", "50.00")]),
            ("AMER", "أمريكانو", "COFFEE", [("", "45.00")]),
            ("CAPP", "كابتشينو", "COFFEE", [("وسط", "60.00"), ("كبير", "75.00")]),
            ("LATTE", "لاتيه", "COFFEE", [("وسط", "65.00"), ("كبير", "80.00")]),
            ("MOCHA", "موكا", "COFFEE", [("", "80.00")]),
            ("MACCH", "ماكياتو", "COFFEE", [("", "55.00")]),
            ("TURK", "قهوة تركي", "COFFEE", [("سادة", "40.00"), ("مضبوط", "40.00")]),
            ("FRENCH", "قهوة فرنساوي", "COFFEE", [("", "55.00")]),
            ("TEA", "شاي", "COFFEE", [("", "25.00")]),
            ("GREENTEA", "شاي أخضر", "COFFEE", [("", "35.00")]),
            ("HERBAL", "أعشاب", "COFFEE", [("ينسون", "30.00"), ("نعناع", "30.00")]),
            ("SAHLAB", "سحلب", "COFFEE", [("", "65.00")]),
            ("HOTCHOC", "هوت شوكليت", "COFFEE", [("", "70.00")]),
        ],
    ),
    (
        "مشروبات باردة",
        "#1f6f8b",
        [
            ("ICEDLAT", "آيس لاتيه", "COLD", [("", "75.00")]),
            ("ICEDAM", "آيس أمريكانو", "COLD", [("", "60.00")]),
            ("FRAPP", "فرابتشينو", "COLD", [("كراميل", "85.00"), ("شوكولاتة", "85.00")]),
            ("MILKSH", "ميلك شيك", "COLD", [("فراولة", "80.00"), ("شوكولاتة", "80.00")]),
            ("MANGO", "عصير مانجو", "COLD", [("", "55.00")]),
            ("ORANGE", "عصير برتقال", "COLD", [("", "50.00")]),
            ("STRAW", "عصير فراولة", "COLD", [("", "55.00")]),
            ("COCKTAIL", "كوكتيل فواكه", "COLD", [("", "70.00")]),
            ("LEMON", "ليمون بالنعناع", "COLD", [("", "45.00")]),
            ("SOFT", "مشروب غازي", "COLD", [("", "30.00")]),
            ("WATER", "مياه معدنية", "COLD", [("صغير", "15.00"), ("كبير", "25.00")]),
        ],
    ),
    (
        "مأكولات",
        "#c77700",
        [
            ("CLUB", "كلوب ساندوتش", "HOT", [("", "150.00")]),
            ("BURGER", "برجر لحم", "HOT", [("سنجل", "165.00"), ("دوبل", "230.00")]),
            ("CHICKSAND", "ساندوتش فراخ", "HOT", [("", "140.00")]),
            ("PASTA", "مكرونة بالفراخ", "HOT", [("", "180.00")]),
            ("PENNE", "بيني بالصوص الأحمر", "HOT", [("", "160.00")]),
            ("GRILL", "صدور مشوية", "HOT", [("", "210.00")]),
            ("FRIES", "بطاطس", "HOT", [("عادي", "60.00"), ("بالجبنة", "85.00")]),
            ("WEDGES", "بطاطس ودجز", "HOT", [("", "70.00")]),
            ("OMLET", "أومليت", "HOT", [("", "90.00")]),
            # Made at the cold bar, sold under food. The category is how the
            # cashier finds it; the station is who makes it.
            ("SALAD", "سلطة سيزر", "COLD", [("", "120.00")]),
            ("TUNASAL", "سلطة تونة", "COLD", [("", "115.00")]),
        ],
    ),
    (
        "حلويات",
        "#c9a227",
        [
            ("CHEESE", "تشيز كيك", "DESSERT", [("", "95.00")]),
            ("BROWNIE", "براوني", "DESSERT", [("", "85.00")]),
            ("WAFFLE", "وافل", "DESSERT", [("نوتيلا", "110.00"), ("عسل", "95.00")]),
            ("PANCAKE", "بان كيك", "DESSERT", [("", "100.00")]),
            ("CREPE", "كريب", "DESSERT", [("نوتيلا", "105.00"), ("فراولة", "115.00")]),
            ("KONAFA", "كنافة بالمانجو", "DESSERT", [("", "120.00")]),
            ("UMALI", "أم علي", "DESSERT", [("", "90.00")]),
            ("ICECREAM", "آيس كريم", "DESSERT", [("كورة", "35.00"), ("٣ كور", "90.00")]),
        ],
    ),
]

#: (code, name, kind, station codes it serves, is the default for its kind)
#:
#: Seeded so the printer registry is populated on a fresh demo rather than being
#: a screen with nothing in it. One receipt roll at the till, and a printer at
#: each bar that makes something — which is what makes the routing rule in
#: `printing/registry.py` observable instead of theoretical.
#:
#: The two bars that make drinks share a printer: they stand next to each other,
#: and a second machine two feet away is a cost with no benefit. That is exactly
#: the case a per-station `printer_name` string could not express and the
#: registry's many-to-many can.
PRINTERS = [
    ("CASHIER", "طابعة الكاشير", "RECEIPT", [], True),
    # The default for KITCHEN, so a ticket from a station nobody assigned still
    # prints somewhere a person is standing rather than waiting in the queue.
    ("BAR", "طابعة البار", "KITCHEN", ["COFFEE", "COLD"], True),
    ("KITCHEN", "طابعة المطبخ", "KITCHEN", ["HOT"], False),
    ("SWEETS", "طابعة الحلويات", "KITCHEN", ["DESSERT"], False),
]

#: sku → {order type: price}. Only where the channel genuinely differs.
#:
#: "المياه جوه الصالة بـ15، ولما تطلع توصيل بتتحسب بـ20."
#:
#: Absolute prices, not a markup, because that is what they are: a delivery
#: price covers a driver and a takeaway cup costs more than a glass that gets
#: washed. Neither is a percentage of the room price, and a cafe that raises
#: dine-in by five pounds does not thereby want delivery to move.
#:
#: Most of the menu is deliberately absent — it costs the same everywhere, and
#: three rows per variant would be three places to forget to update.
CHANNEL_PRICES = {
    "WATER": {"TAKE_AWAY": "20.00", "DELIVERY": "20.00"},
    "SOFT": {"TAKE_AWAY": "35.00", "DELIVERY": "40.00"},
    # A hot drink to go needs a lidded cup, and it does not come back.
    "CAPP": {"TAKE_AWAY": "70.00", "DELIVERY": "80.00"},
    "LATTE": {"TAKE_AWAY": "75.00", "DELIVERY": "85.00"},
    "TURK": {"TAKE_AWAY": "45.00", "DELIVERY": "55.00"},
    "TEA": {"TAKE_AWAY": "30.00", "DELIVERY": "40.00"},
    # Food travels worst and is packed heaviest.
    "CLUB": {"DELIVERY": "170.00"},
    "BURGER": {"DELIVERY": "185.00"},
    "PASTA": {"DELIVERY": "200.00"},
    "FRIES": {"DELIVERY": "75.00"},
}

#: (code, name, base unit, purchase unit, cost per purchase unit, supplier code)
#:
#: The supplier on each line is what makes the purchasing screen mean something:
#: a reorder is placed with the person who sells that thing, and an inventory
#: list where every item comes from one company is a list nobody has to think
#: about — which is a demo that teaches the wrong lesson.
STOCK = [
    ("BEANS", "بن محوج", "G", "KG", "420.00", "ROASTER"),
    ("MILK", "لبن", "ML", "L", "38.00", "DAIRY"),
    ("CREAM", "كريمة خفق", "ML", "L", "95.00", "DAIRY"),
    ("CHEESE", "جبنة شيدر", "G", "KG", "210.00", "DAIRY"),
    ("SUGAR", "سكر", "G", "KG", "32.00", "GROCER"),
    ("CHOC", "شوكولاتة", "G", "KG", "260.00", "GROCER"),
    ("FLOUR", "دقيق", "G", "KG", "26.00", "GROCER"),
    ("TEA", "شاي", "G", "KG", "180.00", "GROCER"),
    ("MANGO", "مانجو مجمدة", "G", "KG", "95.00", "PRODUCE"),
    ("STRAW", "فراولة مجمدة", "G", "KG", "88.00", "PRODUCE"),
    ("ORANGE", "برتقال", "G", "KG", "28.00", "PRODUCE"),
    ("LEMON", "ليمون", "G", "KG", "40.00", "PRODUCE"),
    ("MINT", "نعناع", "G", "KG", "60.00", "PRODUCE"),
    ("POTATO", "بطاطس", "G", "KG", "22.00", "PRODUCE"),
    ("LETTUCE", "خس", "G", "KG", "25.00", "PRODUCE"),
    ("BREAD", "خبز توست", "PC", "PC", "4.00", "BAKERY"),
    ("BUN", "خبز برجر", "PC", "PC", "6.00", "BAKERY"),
    ("CHICKEN", "صدور فراخ", "G", "KG", "180.00", "BUTCHER"),
    ("BEEF", "لحم مفروم", "G", "KG", "340.00", "BUTCHER"),
    ("TUNA", "تونة", "G", "KG", "240.00", "GROCER"),
    ("CUP12", "كوب ١٢ أونصة", "PC", "PC", "1.80", "PACKAGING"),
    ("CUP16", "كوب ١٦ أونصة", "PC", "PC", "2.30", "PACKAGING"),
    ("LID", "غطاء كوب", "PC", "PC", "0.90", "PACKAGING"),
    ("BOX", "علبة تيك أواي", "PC", "PC", "3.50", "PACKAGING"),
    ("NAPKIN", "مناديل", "PC", "PC", "0.15", "PACKAGING"),
]

#: (code, name, phone, payment terms in days, what they sell)
#:
#: Terms differ on purpose and they are the number the purchasing screen exists
#: to surface. A dairy delivering daily is paid on the spot; a roaster invoicing
#: monthly is a real payable that an owner needs to see coming.
SUPPLIERS = [
    ("ROASTER", "محمصة القاهرة للبن", "01004455667", 30),
    ("DAIRY", "ألبان الدلتا", "01112233445", 0),
    ("PRODUCE", "سوق الخضار — أبو أحمد", "01223344556", 0),
    ("BUTCHER", "جزارة الأمانة", "01098877665", 7),
    ("BAKERY", "مخبز الحرمين", "01556677889", 7),
    ("GROCER", "شركة النيل للتوريدات", "01005566778", 14),
    ("PACKAGING", "التعبئة الحديثة", "01277889900", 21),
]

#: variant sku → [(item code, quantity, unit, waste %)]
RECIPES = {
    "CAPP-وسط": [("BEANS", "18", "G", "5"), ("MILK", "150", "ML", "3"), ("CUP12", "1", "PC", "0")],
    "CAPP-كبير": [("BEANS", "22", "G", "5"), ("MILK", "220", "ML", "3"), ("CUP12", "1", "PC", "0")],
    "LATTE-وسط": [("BEANS", "18", "G", "5"), ("MILK", "200", "ML", "3"), ("CUP12", "1", "PC", "0")],
    "ESP-سنجل": [("BEANS", "9", "G", "5")],
    "ESP-دوبل": [("BEANS", "18", "G", "5")],
    "TURK": [("BEANS", "12", "G", "4"), ("SUGAR", "10", "G", "0")],
    "HOTCHOC": [("CHOC", "35", "G", "2"), ("MILK", "200", "ML", "3")],
    "MANGO": [("MANGO", "180", "G", "4")],
    "LEMON": [("LEMON", "60", "G", "8"), ("SUGAR", "20", "G", "0")],
    "CLUB": [("BREAD", "3", "PC", "0"), ("CHICKEN", "120", "G", "6")],
    "BURGER-سنجل": [("BREAD", "1", "PC", "0"), ("BEEF", "150", "G", "8")],
    "BURGER-دوبل": [("BREAD", "1", "PC", "0"), ("BEEF", "300", "G", "8")],
    "FRIES": [("POTATO", "200", "G", "10")],
}

#: (number, seats, shape, span_x, span_y, x, y, rotation)
INSIDE_TABLES = [
    ("1", 2, TableShape.ROUND, 1, 1, 0, 0, 0),
    ("2", 2, TableShape.ROUND, 1, 1, 1, 0, 0),
    ("3", 4, TableShape.SQUARE, 1, 1, 3, 0, 0),
    ("4", 4, TableShape.SQUARE, 1, 1, 4, 0, 0),
    ("5", 6, TableShape.RECT, 2, 1, 6, 0, 0),
    ("6", 4, TableShape.BOOTH, 1, 1, 0, 2, 0),
    ("7", 4, TableShape.BOOTH, 1, 1, 1, 2, 0),
    ("8", 8, TableShape.RECT, 2, 1, 3, 2, 0),
    ("9", 2, TableShape.ROUND, 1, 1, 6, 2, 0),
    ("10", 6, TableShape.BAR, 3, 1, 0, 4, 0),
]

TERRACE_TABLES = [
    ("11", 4, TableShape.ROUND, 1, 1, 0, 0, 0),
    ("12", 4, TableShape.ROUND, 1, 1, 2, 0, 15),
    ("13", 2, TableShape.SQUARE, 1, 1, 4, 0, 0),
    ("14", 6, TableShape.RECT, 2, 1, 0, 2, 0),
    ("15", 4, TableShape.ROUND, 1, 1, 3, 2, 345),
    ("16", 2, TableShape.SQUARE, 1, 1, 5, 2, 0),
]

GUARDIANS = [
    ("أحمد محمود", "01001234567", [("يوسف", 5, "حساسية من الفول السوداني")]),
    ("منى عبد الله", "01112345678", [("جنى", 4, ""), ("مالك", 7, "")]),
    ("طارق سمير", "01223456789", [("ليلى", 6, "")]),
    ("هبة علي", "01098765432", [("آدم", 3, "ربو خفيف — البخاخة مع الأم")]),
    ("خالد فتحي", "01187654321", [("سلمى", 8, "")]),
]


class Command(BaseCommand):
    help = "Populate a demo cafe: menu, floor, stock, staff, and a fortnight of trading."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14, help="Trading days to generate.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even though orders already exist. Mixes demo data into real trading.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="DELETE the demo organization first, then seed it fresh. Demo databases only.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self._reset()
        elif Order.objects.exists() and not options["force"]:
            raise CommandError(
                f"This database already holds {Order.objects.count()} orders. Seeding would mix "
                "demo trading into a real ledger, and nothing afterwards can tell the two apart. "
                "Re-run with --force only on a database you are willing to lose, or --reset to "
                "delete the demo organization and start it over."
            )

        self.random = random.Random(SEED)  # noqa: S311 — presentation data, not security
        self.counter = 0

        self.license_keys: dict[str, str] = {}

        with transaction.atomic():
            org, branches = self._organization()
            self._relax_mfa(org)
            roles = ensure_system_roles(org)
            units = self._units(org)
            staff = self._staff(org, branches, roles)

            # Everything below is branch-scoped in the schema, so it is built per
            # branch rather than once: its own catalogue, stock, suppliers, floor,
            # kitchen stations, printers, kids area and licence. A branch that
            # shared the main branch's rows would be a branch that cannot be told
            # apart from it, which is the opposite of what a second branch is for.
            built = []
            for branch in branches:
                stations = self._stations(org, branch)
                self._printers(org, branch, stations)
                suppliers = self._suppliers(org, branch)
                items = self._stock(org, branch, units, suppliers)
                menu = self._menu(org, branch, stations)
                self._channel_prices(org, branch)
                self._modifiers(org, branch, items)
                self._recipes(menu, items, units)
                methods = self._payment_methods(org, branch)
                self._floor(org, branch)
                self._receive_stock(
                    org, branch, suppliers["GROCER"], items, units, staff["store@caesar.test"]
                )
                self._kids(org, branch, menu)
                # After the receipt, because that is what recosts the recipes.
                # Anything still at zero has no recipe to cost it, and a zero cost
                # is not a neutral placeholder — it is a claim of 100% margin.
                self._backfill_costs(menu)
                self._license(org, branch, staff["owner@caesar.test"])
                built.append((branch, menu, methods))

        # Trading runs outside the setup transaction: a fortnight is thousands
        # of rows per branch, and one long transaction holding every lock is how a
        # seed command times out on a machine that is also serving a dev server.
        days = 0
        for branch, menu, methods in built:
            weight = self._weight(branch.code)
            days = self._trade(branch, menu, methods, staff, options["days"], weight=weight)
            self._seat_the_room(branch, staff)
            self._live_kitchen(branch, menu, staff)
            self._children_inside(branch, staff)

        self._summary(branches, days)

    @staticmethod
    def _weight(code: str) -> Decimal:
        """How busy this branch is, relative to the main one."""
        for branch_code, *_rest, weight in BRANCHES:
            if branch_code == code:
                return weight
        return Decimal("1.0")

    # ── the cafe ─────────────────────────────────────────────────────────────

    def _reset(self) -> None:
        """
        Empty the demo organization's trading, then let the seed refill it.

        `--force` answers a different question ("mix demo data into a ledger
        that already has trading") and deliberately deletes nothing. Re-seeding
        needs the opposite. Without a reset the second run collides on
        `uniq_local_number_per_branch` — the invoice counter restarts at 1 while
        yesterday's MB-01-00001 is still there — so a command written to be run
        repeatedly could only ever be run once.

        **Deleted in an explicit order rather than with `org.delete()`.** Almost
        every foreign key into an organization is `PROTECT`, on purpose: an org
        with financial history must not vanish because somebody deleted a row
        two tables away. A cascade here would be that same accident with a
        friendlier name. Naming each model in dependency order keeps the list
        reviewable, and a new model that this misses fails loudly on the next
        reset rather than quietly leaving half a cafe behind.

        **The catalogue goes too, and that is not the same call as the rest.**
        Staff, floor, stock items and units are things a person may have edited
        in the demo and would be annoyed to lose. The menu is not: it is defined
        by the `MENU` table in this file, the seed owns it outright, and
        `get_or_create` adopting a STALE menu is a real bug rather than a
        courtesy — adding a second variant to a product whose old single variant
        is still flagged default collides on `one_default_variant_per_product`,
        and the seed refuses to run at all. Data this command authors, this
        command rebuilds.
        """
        org = Organization.objects.filter(name_ar="كافيه القيصر").first()
        if org is None:
            return

        from apps.audit.models import AuditLog
        from apps.floor.models import TableSession
        from apps.inventory.models import StockMovement
        from apps.kids.models import PlaySession
        from apps.orders.models import OrderEvent, OrderItem
        from apps.payments.models import Invoice, Payment
        from apps.reporting.models import HourlyDaily, ProductDaily, SalesDaily
        from apps.shifts.models import CashMovement, Shift
        from apps.sync.models import ChangeLog, DeviceCursor

        branches = list(org.branches.values_list("id", flat=True))
        orders = Order.objects.filter(organization=org)

        # Children first, then their parents. Anything referencing an order has
        # to go before the order does, or PROTECT stops us mid-way and leaves
        # the database in a state that is neither seeded nor reset.
        Payment.objects.filter(order__in=orders).delete()
        Invoice.objects.filter(order__in=orders).delete()
        OrderEvent.objects.filter(order__in=orders).delete()
        OrderItem.objects.filter(order__in=orders).delete()
        PlaySession.objects.filter(organization=org).delete()
        StockMovement.objects.filter(branch_id__in=branches).delete()

        # Purchasing goes with the movements, and forgetting it was a real bug.
        # `_receive_stock` skips itself when the branch already has a receipt —
        # correct, so a re-run does not double the stock. But the reset was
        # clearing the MOVEMENTS and leaving the RECEIPT, so the guard tripped,
        # no stock was received, `post_receipt` never ran, and nothing recosted
        # the recipes. Every variant kept a cost of zero and the dashboard
        # reported a 100% margin on a fortnight of trading.
        SupplierLedgerEntry.objects.filter(supplier__branch_id__in=branches).delete()
        GRLine.objects.filter(receipt__branch_id__in=branches).delete()
        GoodsReceipt.objects.filter(branch_id__in=branches).delete()
        PurchaseOrder.objects.filter(branch_id__in=branches).delete()
        removed = orders.count()
        orders.delete()

        TableSession.objects.filter(table__area__branch_id__in=branches).delete()
        CashMovement.objects.filter(shift__branch_id__in=branches).delete()
        Shift.objects.filter(organization=org).delete()
        for rollup in (SalesDaily, ProductDaily, HourlyDaily):
            rollup.objects.filter(branch_id__in=branches).delete()
        ChangeLog.objects.filter(branch_id__in=branches).delete()

        # Licensing, in dependency order rather than by cascade.
        #
        # `InvoiceBlock.device` is PROTECT, so the blocks go before the devices
        # that reserved them — a device cannot be deleted while it still holds a
        # range of invoice numbers, which is the right rule for a live cafe and
        # means a reset has to name it. `LicenseEvent` would cascade off the
        # licence, but it is deleted explicitly for the same reason every other
        # level here is: a cascade is the accident with a friendlier name.
        #
        # The devices have to go. A browser or a terminal holds a device secret
        # for a licence that no longer exists, and leaving the rows behind would
        # let a stale credential keep answering while the licence it belongs to
        # is gone — a re-seeded demo that still lets an old till sell.
        InvoiceBlock.objects.filter(device__branch_id__in=branches).delete()
        DeviceCursor.objects.filter(device__branch_id__in=branches).delete()
        LicenseEvent.objects.filter(license__organization=org).delete()
        Device.objects.filter(branch_id__in=branches).delete()
        License.objects.filter(organization=org).delete()

        # The catalogue, which this command authors — bottom up, because every
        # link in the chain is PROTECT. That is the right setting for a live
        # cafe (a product that has ever been sold must not vanish and orphan its
        # line items) and it means a reset has to name each level rather than
        # lean on a cascade that deliberately is not there.
        #
        # A play area points at the variant its sessions are billed as, and a
        # recipe at the variant it makes; both let go first.
        PlayArea.objects.filter(branch_id__in=branches).update(
            billing_variant=None, socks_variant=None
        )
        Recipe.objects.filter(variant__product__branch_id__in=branches).delete()
        Printer.all_objects.filter(branch_id__in=branches).delete()
        VariantChannelPrice.objects.filter(variant__product__branch_id__in=branches).delete()
        ProductVariant.objects.filter(product__branch_id__in=branches).delete()
        Product.all_objects.filter(branch_id__in=branches).delete()
        Category.all_objects.filter(branch_id__in=branches).delete()
        Modifier.objects.filter(group__branch_id__in=branches).delete()
        ModifierGroup.objects.filter(branch_id__in=branches).delete()

        # The audit trail is NOT touched. `AuditLog.delete()` raises on purpose,
        # and a reset command that reached around that would be the first crack
        # in the one record whose whole value is that nothing can edit it — a
        # demo convenience is nowhere near a good enough reason. The leftover
        # rows name orders that no longer exist, which is untidy and harmless:
        # each row carries its own `object_label`, so it still reads correctly.
        kept = AuditLog.objects.filter(branch_id__in=branches).count()

        # Branches the seed no longer defines.
        #
        # The reset empties each branch's trading but had no reason to touch the
        # `Branch` rows themselves — so shrinking BRANCHES from three back to one
        # left two standing with everything behind them deleted. An active branch
        # with nothing in it is the worst shape a branch can be in: it still scopes
        # queries, still counts as a branch anywhere the code iterates them, and
        # every figure it produces is zero. Zero reads as "a quiet week", not as
        # "this branch should not exist".
        #
        # **Deactivated, not deleted**, and that is the model's own rule rather than
        # a shortcut. `Branch` is a `SoftDeletableModel`, and Station, InventoryItem,
        # Supplier, Area, PaymentMethod, PlayArea and Guardian all PROTECT it — the
        # first attempt at a hard delete collected ninety-odd protected rows. Naming
        # each of them here would be a list that silently rots the next time any
        # model gains a branch foreign key, and it would be a demo command asserting
        # it knows better than a constraint the schema states deliberately.
        #
        # Only branches this command created and has since dropped. A branch still
        # in BRANCHES is untouched, and so is any branch of another organisation —
        # on a shared database that scope is the difference between tidying up after
        # yourself and deleting somebody's café.
        defined = {code for code, *_rest in BRANCHES}
        stale = Branch.objects.filter(organization=org, is_active=True).exclude(code__in=defined)
        retired = [b.code for b in stale]
        stale.update(is_active=False, deactivated_at=timezone.now())

        self.stdout.write(
            f"Reset: removed {removed} demo orders and their trading. "
            f"Kept {kept} audit rows — that log is append-only."
        )
        if retired:
            self.stdout.write(
                f"Deactivated {len(retired)} branch(es) the seed no longer defines: "
                + ", ".join(retired)
                + " — their history is protected, so they are retired rather than removed."
            )

    def _organization(self) -> tuple[Organization, Branch]:
        org, _ = Organization.objects.get_or_create(
            name_ar="كافيه القيصر",
            defaults={
                "name_en": "Caesar Cafe",
                "phone": "0132600000",
                "tax_number": "421-885-903",
            },
        )
        branches = []
        for code, name_ar, name_en, phone, address, _weight in BRANCHES:
            branch, _ = Branch.objects.get_or_create(
                organization=org,
                code=code,
                defaults={
                    "name_ar": name_ar,
                    "name_en": name_en,
                    "phone": phone,
                    "address": address,
                },
            )
            branches.append(branch)
        return org, branches

    def _relax_mfa(self, org) -> None:
        """
        Turn off mandatory two-factor **for the demo organisation only**.

        `security.require_mfa_for_roles` defaults to SUPER_ADMIN and
        BRANCH_MANAGER, and it should: those accounts are reachable from the
        internet and a password alone is not enough for them. But a demo where
        the first thing anybody meets is an enrolment screen is a demo nobody
        gets past, and typing a TOTP code to look at a floor plan teaches
        nothing about the floor plan.

        Written through the settings registry rather than around it, so the
        change is validated, audited, and appears in the settings screen where
        somebody can see that it was done. `bootstrap` — what a real cafe runs —
        does not do this, and turning it back on is one switch in that screen.
        """
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        resolver.set_value(
            "security.require_mfa_for_roles", [], scope=Scope.ORGANIZATION, scope_id=org.id
        )

    def _staff(self, org, branches, roles) -> dict[str, User]:
        people: dict[str, User] = {}

        for email, name, role_code, pin in STAFF:
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(
                    email=email,
                    password=DEMO_PASSWORD,
                    organization=org,
                    full_name_ar=name,
                    is_staff=role_code == "SUPER_ADMIN",
                )
            user.set_pin(pin)
            user.save(update_fields=["pin_hash", "pin_set_at"])

            # A role at every branch, not just the first.
            #
            # A cashier assigned only to the main branch cannot open a till at the
            # other two, and the demo would show two branches full of data that
            # nobody can log into — which looks like a permissions bug in the
            # product rather than a gap in the seed. A real chain does staff it
            # per site; a demo needs every account to work everywhere it is
            # offered, because the sign-in screen offers them all.
            if role_code == "SUPER_ADMIN":
                RoleAssignment.objects.get_or_create(user=user, role=roles[role_code], branch=None)
            else:
                for branch in branches:
                    RoleAssignment.objects.get_or_create(
                        user=user, role=roles[role_code], branch=branch
                    )
            people[email] = user

        return people

    def _license(self, org, branch, owner) -> None:
        """
        A licence for the branch, so a terminal can actually be activated.

        Without this the demo seeded a whole cafe and then locked the till: ten
        staff with a PIN each, and no way for any of them to reach a keypad,
        because the POS opens for nothing without a valid licence (C5). The
        activation screen is the first thing anyone sees after `seed_demo`, and
        the only thing that got them past it was hand-issuing a licence through
        the API.

        **The key is random, not a constant in this file, and that is deliberate
        even though everything else about this command is deterministic.**
        `keys.generate` uses `secrets` because `random` is a Mersenne Twister and
        observing a few outputs predicts the rest — `apps/licensing/keys.py` says
        so at the top, and routing around it for a demo would make this file the
        counter-example to its own module's argument. The determinism that
        matters here is the trading data, which is what somebody debugs; a
        credential is not. And `--reset` deletes the devices along with the
        licence, so a re-seed forces a re-activation regardless — a fixed key
        would have saved copying a string, not a step.

        Issued through the real service, like everything else this command
        drives, so the LicenseEvent and the audit row exist too.
        """
        issued = licensing_services.issue_license(
            organization=org,
            branch=branch,
            license_type=LicenseType.LIFETIME,
            # Eight. One café runs on more machines than people expect — two
            # tills, the office, the manager's laptop, a kitchen screen — and a
            # tight seat limit turns "activate the second terminal" into a
            # support question on the first day. Seats exist to stop one licence
            # running two cafés, not to ration a café's own machines.
            max_devices=8,
            notes="Seeded by seed_demo. Demo use only.",
            actor=owner,
        )
        # Held for `_summary`: this is the ONLY moment the plaintext exists.
        # `issue_license` stores a hash, so a key not printed now is a licence
        # nobody can ever activate against.
        self.license_key = issued.plaintext_key
        self.license_keys[branch.code] = issued.plaintext_key

    def _stations(self, org, branch) -> dict[str, Station]:
        """
        `update_or_create`, not `get_or_create`.

        A station is seed-owned data like the menu is: renaming one in the
        `STATIONS` table above and having the database keep the old name is not
        a courtesy, it is the seed lying about what it just built. Deleting them
        instead would take the kitchen tickets with them, which is a worse
        answer for a rename.
        """
        return {
            code: Station.objects.update_or_create(
                organization=org,
                branch=branch,
                code=code,
                defaults={
                    "name_ar": name,
                    "target_prep_minutes": target,
                    "auto_accept": auto,
                    "sort_order": index,
                },
            )[0]
            for index, (code, name, target, auto) in enumerate(STATIONS)
        }

    def _printers(self, org, branch, stations) -> None:
        """
        Give the branch its printers, so the registry screen has something true
        on it and firing an order actually routes somewhere.

        The device paths are the Linux defaults, which will be wrong on a real
        Windows till — deliberately. Each terminal overrides its own port from
        the local binding screen, because a serial port is a property of a
        machine and not of a cafe, and a branch-wide guess would be wrong on two
        terminals out of three.
        """
        for index, (code, name, kind, station_codes, is_default) in enumerate(PRINTERS):
            printer, _ = Printer.objects.get_or_create(
                organization=org,
                branch=branch,
                code=code,
                defaults={
                    "name_ar": name,
                    "kind": kind,
                    "connection": "USB",
                    "device_path": f"/dev/usb/lp{index}",
                    "paper_width_mm": 80,
                    "is_default": is_default,
                },
            )
            printer.stations.set([stations[c] for c in station_codes])

    def _units(self, org) -> dict[str, Unit]:
        specs = [
            ("KG", "كيلو", 3),
            ("G", "جرام", 0),
            ("L", "لتر", 3),
            ("ML", "مللي", 0),
            ("PC", "قطعة", 0),
        ]
        units = {
            code: Unit.objects.get_or_create(
                organization=org, code=code, defaults={"name_ar": name, "decimal_places": places}
            )[0]
            for code, name, places in specs
        }
        for big, small in [("KG", "G"), ("L", "ML")]:
            UnitConversion.objects.get_or_create(
                from_unit=units[big], to_unit=units[small], defaults={"factor": Decimal("1000")}
            )
        return units

    def _stock(self, org, branch, units, suppliers) -> dict[str, InventoryItem]:
        items = {}
        for code, name, base, _purchase, _cost, supplier_code in STOCK:
            bulk = base in ("G", "ML")
            items[code], _ = InventoryItem.objects.update_or_create(
                organization=org,
                branch=branch,
                code=code,
                defaults={
                    "name_ar": name,
                    "base_unit": units[base],
                    # Packaging is not an ingredient. Filed correctly so a COGS
                    # report can separate what went into the cup from the cup.
                    "item_type": ItemType.PACKAGING
                    if supplier_code == "PACKAGING"
                    else ItemType.RAW,
                    "default_supplier": suppliers[supplier_code],
                    # `minimum_stock` is what the low-stock warning fires on, and
                    # it sits BELOW the reorder level on purpose: reorder is
                    # "time to buy", minimum is "you are about to run out". One
                    # number doing both jobs means either constant noise or a
                    # warning that arrives after the coffee has run out.
                    "minimum_stock": Decimal("1000") if bulk else Decimal("20"),
                    "reorder_level": Decimal("2000") if bulk else Decimal("40"),
                    "reorder_quantity": Decimal("10000") if bulk else Decimal("200"),
                },
            )
        return items

    def _suppliers(self, org, branch) -> dict[str, Supplier]:
        return {
            code: Supplier.objects.update_or_create(
                organization=org,
                branch=branch,
                name=name,
                defaults={"phone": phone, "payment_terms_days": terms},
            )[0]
            for code, name, phone, terms in SUPPLIERS
        }

    def _channel_prices(self, org, branch) -> None:
        """
        The prices that differ when an order leaves the room.

        Written against the DEFAULT variant of each product, because that is the
        one a tap rings and the one the tile shows. A cafe that wants the large
        size priced separately per channel can add it in the Web Admin; seeding
        every size on every channel would be a wall of rows that teaches nothing.

        **Resolved from the PRODUCT's sku, not the variants dict.** That dict is
        keyed `WATER-صغير` for anything with sizes, so looking `WATER` up in it
        found nothing — and the first version of this skipped the miss quietly
        and seeded six of the ten channel prices. A silent `continue` over a
        table somebody hand-wrote is how a demo ends up not demonstrating the
        feature it was extended for, so an unknown sku is now an error.
        """
        for sku, by_channel in CHANNEL_PRICES.items():
            variant = (
                ProductVariant.objects.filter(
                    product__branch=branch, product__sku=sku, is_default=True
                )
                .order_by("sort_order")
                .first()
            )
            if variant is None:
                raise CommandError(
                    f"CHANNEL_PRICES names '{sku}', which is not a product in MENU. "
                    "Fix the table rather than letting the price quietly not exist."
                )

            for order_type, price in by_channel.items():
                VariantChannelPrice.objects.update_or_create(
                    variant=variant,
                    order_type=order_type,
                    defaults={"price": Decimal(price)},
                )

    def _menu(self, org, branch, stations) -> dict[str, ProductVariant]:
        variants: dict[str, ProductVariant] = {}

        for order, (category_name, colour, products) in enumerate(MENU):
            category, _ = Category.objects.get_or_create(
                organization=org,
                branch=branch,
                name_ar=category_name,
                defaults={"color": colour, "sort_order": order},
            )
            for p_index, (sku, name, station_code, sizes) in enumerate(products):
                product, _ = Product.objects.get_or_create(
                    organization=org,
                    branch=branch,
                    sku=sku,
                    defaults={
                        "category": category,
                        "station": stations[station_code],
                        "name_ar": name,
                        "sort_order": p_index,
                    },
                )
                for v_index, (variant_name, price) in enumerate(sizes):
                    key = f"{sku}-{variant_name}" if variant_name else sku
                    variants[key], _ = ProductVariant.objects.get_or_create(
                        product=product,
                        sku=key,
                        defaults={
                            "name_ar": variant_name,
                            "price": Decimal(price),
                            "is_default": v_index == 0,
                            "sort_order": v_index,
                        },
                    )
        return variants

    def _modifiers(self, org, branch, items) -> None:
        group, _ = ModifierGroup.objects.get_or_create(
            organization=org,
            branch=branch,
            name_ar="إضافات القهوة",
            defaults={"max_select": 4},
        )
        for name, delta, item_code, consumed in [
            ("شوت إسبريسو زيادة", "15.00", "BEANS", "9"),
            ("لبن خالي الدسم", "0.00", None, "0"),
            ("كراميل", "10.00", None, "0"),
            ("بدون سكر", "0.00", None, "0"),
        ]:
            Modifier.objects.get_or_create(
                group=group,
                name_ar=name,
                defaults={
                    "price_delta": Decimal(delta),
                    "inventory_item": items.get(item_code) if item_code else None,
                    "quantity_consumed": Decimal(consumed),
                },
            )

    def _recipes(self, menu, items, units) -> None:
        for variant_key, lines in RECIPES.items():
            variant = menu.get(variant_key)
            if variant is None:
                continue
            recipe, created = Recipe.objects.get_or_create(variant=variant)
            if not created:
                continue
            RecipeLine.objects.bulk_create(
                RecipeLine(
                    recipe=recipe,
                    item=items[code],
                    quantity=Decimal(quantity),
                    unit=units[unit],
                    waste_percent=Decimal(waste),
                )
                for code, quantity, unit, waste in lines
            )

    #: Food cost as a share of menu price, by the kind of thing being sold.
    #:
    #: Real café figures: drinks run cheap because the cost is a spoon of beans and
    #: some milk, food runs dearer because it is mostly ingredient. These are the
    #: fallback for a variant with no recipe — the 13 items that DO have one are
    #: costed properly, from an actual goods receipt through weighted average.
    COST_RATIOS = (
        ("مشروب", Decimal("0.22")),
        ("قهوة", Decimal("0.22")),
        ("عصير", Decimal("0.30")),
        ("حلويات", Decimal("0.33")),
        ("ساندويتش", Decimal("0.38")),
        ("وجبات", Decimal("0.38")),
    )
    DEFAULT_COST_RATIO = Decimal("0.30")

    def _backfill_costs(self, menu) -> None:
        """
        Give every variant a cost, so the P&L is not a fantasy.

        Only 13 of 56 variants have a recipe, and a variant's cost is computed from
        its recipe when stock is received. The other 43 kept `cost = 0`, the order
        service snapshotted that faithfully, and a fortnight of trading reported a
        **94% gross margin** — 712,437 in sales against 41,067 of cost. Every
        report built on cost was wrong at once: the P&L, product profitability, the
        margin column, the dashboard.

        Nobody had noticed because zero reads as "not filled in yet" rather than as
        the assertion it is. `_reset` even carries a comment about the same symptom
        from a different cause, which says something about how this kind of number
        hides: it is not a crash, and it is not obviously absurd unless you know
        what a café's margin looks like.

        An approximation is the right fix for demo data. The alternative is
        inventing ingredient lines for 43 items, which is more fiction with extra
        steps — and it would still be an approximation, just a better-hidden one.
        Anything with a real recipe is left alone.
        """
        # Re-read from the database rather than trusting `menu`.
        #
        # `_receive_stock` recosts the recipe-backed variants with an UPDATE, so the
        # objects in `menu` still carry the cost they were built with — zero. Using
        # them would overwrite a cost computed from a real weighted average with a
        # flat guess, and it would look like it worked: every variant would end up
        # with a plausible non-zero number.
        fresh = ProductVariant.objects.filter(id__in=[v.id for v in menu.values()]).select_related(
            "product__category"
        )

        updated = []
        for variant in fresh:
            if variant.cost and variant.cost > Decimal("0"):
                continue
            category = getattr(variant.product.category, "name_ar", "") or ""
            ratio = next(
                (r for key, r in self.COST_RATIOS if key in category), self.DEFAULT_COST_RATIO
            )
            variant.cost = (variant.price * ratio).quantize(Decimal("0.01"))
            updated.append(variant)

        if updated:
            ProductVariant.objects.bulk_update(updated, ["cost"])
            # `menu` is what `_trade` sells from, and the order service snapshots
            # `variant.cost` onto each line. Leaving the in-memory copies stale
            # would write the fortnight of trading at zero cost anyway.
            costs = {v.id: v.cost for v in updated}
            for variant in menu.values():
                if variant.id in costs:
                    variant.cost = costs[variant.id]

    def _payment_methods(self, org, branch) -> dict[str, PaymentMethod]:
        specs = [("CASH", "نقدي", True), ("CARD", "بطاقة", False), ("WALLET", "محفظة", False)]
        return {
            code: PaymentMethod.objects.get_or_create(
                organization=org,
                branch=branch,
                code=code,
                defaults={"name_ar": name, "counts_as_cash": cash},
            )[0]
            for code, name, cash in specs
        }

    def _floor(self, org, branch) -> None:
        for order, (area_name, tables) in enumerate(
            [("الصالة الداخلية", INSIDE_TABLES), ("التراس", TERRACE_TABLES)]
        ):
            area, _ = Area.objects.get_or_create(
                organization=org,
                branch=branch,
                name_ar=area_name,
                defaults={"sort_order": order},
            )
            for number, seats, shape, span_x, span_y, x, y, rotation in tables:
                Table.objects.get_or_create(
                    area=area,
                    number=number,
                    defaults={
                        "seats": seats,
                        "shape": shape,
                        "span_x": span_x,
                        "span_y": span_y,
                        "pos_x": x,
                        "pos_y": y,
                        "rotation": rotation,
                    },
                )

    def _receive_stock(self, org, branch, supplier, items, units, user) -> None:
        """
        Through `post_receipt`, so stock levels, weighted-average costs, the
        supplier ledger and every recipe's cost all follow from one real
        operation rather than being written by hand and disagreeing.
        """
        from apps.purchasing import services as purchasing

        if GoodsReceipt.objects.filter(branch=branch).exists():
            return

        receipt = GoodsReceipt.objects.create(
            organization=org,
            branch=branch,
            supplier=supplier,
            grn_number="GRN-0001",
            supplier_invoice_no="INV-2026-118",
            received_date=timezone.localdate() - timedelta(days=15),
        )
        for code, _name, _base, purchase_unit, cost, _supplier in STOCK:
            GRLine.objects.create(
                receipt=receipt,
                item=items[code],
                unit=units[purchase_unit],
                quantity_received=Decimal("40") if purchase_unit in ("KG", "L") else Decimal("500"),
                unit_cost=Decimal(cost),
            )
        purchasing.post_receipt(receipt, user=user)

    def _kids(self, org, branch, menu) -> None:
        area, _ = PlayArea.objects.get_or_create(
            organization=org,
            branch=branch,
            name_ar="صالة الأطفال",
            defaults={"max_capacity": 25, "billing_variant": menu.get("WAFFLE")},
        )
        for name, mode, entry, included, package, block_minutes, block_rate, cap in [
            ("عداد بالساعة", TariffMode.TIMED, "25.00", 30, 0, 15, "15.00", "120.00"),
            ("باقة ساعتين", TariffMode.PACKAGE, "90.00", 0, 120, 15, "15.00", "150.00"),
            ("يوم مفتوح", TariffMode.OPEN_DAY, "150.00", 0, 0, 0, "0.00", "150.00"),
        ]:
            PlayTariff.objects.get_or_create(
                area=area,
                name_ar=name,
                defaults={
                    "mode": mode,
                    "entry_fee": Decimal(entry),
                    "included_minutes": included,
                    "package_minutes": package,
                    "block_minutes": block_minutes,
                    "block_rate": Decimal(block_rate),
                    "grace_minutes": 5,
                    "daily_cap": Decimal(cap),
                },
            )

        from apps.kids.models import Child, Guardian

        for full_name, phone, children in GUARDIANS:
            guardian, _ = Guardian.objects.get_or_create(
                organization=org, branch=branch, phone=phone, defaults={"full_name": full_name}
            )
            for child_name, years, notes in children:
                Child.objects.get_or_create(
                    guardian=guardian,
                    first_name=child_name,
                    defaults={
                        "age_months_snapshot": years * 12,
                        "medical_notes": notes,
                        "consent_recorded": True,
                    },
                )

    # ── trading ──────────────────────────────────────────────────────────────

    def _trade(self, branch, menu, methods, staff, days: int, weight: Decimal | None = None) -> int:
        """
        A fortnight of sales, through the real order and payment services.

        Volume follows the shape of a real cafe day — a morning coffee run, a
        quiet afternoon, a heavy evening — because a flat distribution makes the
        hourly report look broken and hides whether it works.

        `weight` scales the whole branch: 1.0 for the main one, less for the others.
        Three branches trading identically would make every branch comparison look
        correct whichever column it sorted by.
        """
        from apps.orders import services as order_services
        from apps.payments import services as payment_services

        sellable = list(menu.values())
        cashiers = [staff["cashier@caesar.test"], staff["cashier2@caesar.test"]]
        weights = {  # hour → orders
            8: 6,
            9: 10,
            10: 9,
            11: 7,
            12: 8,
            13: 11,
            14: 9,
            15: 6,
            16: 7,
            17: 10,
            18: 14,
            19: 18,
            20: 20,
            21: 17,
            22: 11,
        }

        for day_offset in range(days, 0, -1):
            day = timezone.localdate() - timedelta(days=day_offset)
            cashier = cashiers[day_offset % 2]
            shift = self._shift_for(branch, day, cashier)

            for hour, count in weights.items():
                # Weekends are busier. A report that cannot show that is not
                # telling the owner anything they could act on.
                busy = 1.4 if day.weekday() in (4, 5) else 1.0
                # `max(1, ...)` so the quietest branch still trades every hour it
                # is open. A branch with empty hours would make the hourly report
                # look like it lost data rather than like a slow afternoon.
                scaled = max(1, int(count * busy * float(weight or 1)))
                for _ in range(scaled):
                    self._one_sale(
                        branch,
                        sellable,
                        methods,
                        cashier,
                        shift,
                        day,
                        hour,
                        order_services,
                        payment_services,
                    )

            shift.refresh_from_db()
            if shift.status == "OPEN":
                from apps.shifts import services as shift_services

                expected = shift_services.compute_totals(shift).expected_cash
                # A small, occasional discrepancy. A drawer that balances to the
                # piaster every single day for a fortnight is not a cafe, and it
                # leaves the variance report with nothing to show.
                drift = Decimal(self.random.choice(["0.00", "0.00", "0.00", "-15.00", "25.00"]))
                shift_services.close_shift(
                    shift=shift,
                    counted_cash=expected + drift,
                    reason="فرق في الفكة" if drift else "",
                    user=cashier,
                )

        # ── today, up to now ────────────────────────────────────────────────
        #
        # The loop above stops at yesterday, which left the dashboard's hero
        # figure, its hourly chart and its top-products list all empty on a
        # fresh seed — the three things anybody looks at first. A demo whose
        # headline number is 0.00 does not demonstrate a dashboard.
        #
        # Only the hours that have ALREADY PASSED. Selling at 20:00 when it is
        # 10am would put sales in the future: the hourly chart would show a
        # peak that has not happened, and the business-day boundary would sort
        # them into a day that has not started.
        #
        # This shift is left OPEN on purpose. A cafe at 3pm has a drawer open,
        # and it is what makes the till's shift screen show something real.
        now_hour = timezone.localtime().hour
        today = timezone.localdate()
        cashier = cashiers[0]
        open_shift = self._shift_for(branch, today, cashier)

        for hour, count in weights.items():
            if hour > now_hour:
                continue
            for _ in range(int(count * 0.9)):
                self._one_sale(
                    branch,
                    sellable,
                    methods,
                    cashier,
                    open_shift,
                    today,
                    hour,
                    order_services,
                    payment_services,
                )

        self._rebuild_rollups(branch, days)
        return days

    def _rebuild_rollups(self, branch, days: int) -> None:
        """
        Recompute every day this command just wrote into.

        **A rollup is a cache that cannot tell it is stale.** `_ensure_rollups`
        builds a closed day only when its row is MISSING — which is right for a
        real cafe, where a closed day never gains orders afterwards.

        This command breaks that assumption on purpose: it back-dates a
        fortnight of trading into days that are already closed. If any row for
        those dates exists first — left by a previous run, or written by the
        nightly Celery task while `--reset` had the orders deleted — it says
        zero, and nothing will ever correct it. Every historical report then
        reads zero on top of a database full of sales, which is exactly what
        happened here: a stored 0.00 against 158 real settled orders.

        `build_day` is delete-then-insert, so this is idempotent and cheap.
        """
        from apps.reporting import business_day, rollups

        today = business_day.today(branch)
        for offset in range(days, -1, -1):
            rollups.build_day(branch, today - timedelta(days=offset))

    def _shift_for(self, branch, day: date, cashier) -> Shift:
        from apps.shifts import services as shift_services

        opened = timezone.make_aware(datetime.combine(day, time(hour=8)))
        shift = shift_services.open_shift(
            branch=branch, user=cashier, opening_cash=Decimal("500.00")
        )
        Shift.objects.filter(pk=shift.pk).update(opened_at=opened)
        shift.refresh_from_db()
        return shift

    def _one_sale(
        self,
        branch,
        sellable,
        methods,
        cashier,
        shift,
        day,
        hour,
        order_services,
        payment_services,
    ) -> None:
        placed = timezone.make_aware(
            datetime.combine(day, time(hour=hour, minute=self.random.randint(0, 59)))
        )
        # From the enum, never a string literal.
        #
        # This read "TAKEAWAY" — no underscore — and the enum member is
        # TAKE_AWAY. Nothing rejected it: `order_type` is a CharField with
        # choices, and Django does not enforce choices on `.create()`. So a
        # quarter of every seeded day carried a channel that matches no channel
        # price, groups under no channel in a report, and renders in the SPA as
        # the raw word TAKEAWAY. It survived because it looked right in a diff.
        #
        # Found by the check that now refuses a disabled channel — the first
        # thing that ever compared this string against the real set.
        order_type = self.random.choices(
            [OrderType.DINE_IN, OrderType.TAKE_AWAY, OrderType.DELIVERY, OrderType.EXTERNAL],
            weights=[64, 22, 6, 8],
        )[0]

        # Numbered explicitly. The server's generator counts today's orders, and
        # these are backdated after creation — so left to itself it would hand
        # every one of them `MB-00-0001` and collide on the second sale.
        self.counter += 1
        order = order_services.open_order(
            branch=branch,
            order_type=order_type,
            shift=shift,
            user=cashier,
            local_number=f"MB-01-{self.counter:05d}",
        )
        Order.objects.filter(pk=order.pk).update(opened_at=placed, created_at=placed)

        events = []
        for _ in range(self.random.randint(1, 4)):
            variant = self.random.choice(sellable)
            events.append(
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_ADDED,
                    "payload": {
                        "line_id": str(uuid.uuid4()),
                        "variant_id": str(variant.id),
                        "quantity": str(self.random.choice([1, 1, 1, 2])),
                    },
                }
            )

        # One order in twenty gets a discount, so the discount report and the
        # audit trail both have real entries rather than being empty screens.
        if self.random.random() < 0.05:
            events.append(
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.DISCOUNT_APPLIED,
                    "payload": {"percent": "10", "reason": "عميل دائم"},
                }
            )

        order_services.apply_events(order, events, actor=cashier)
        order.refresh_from_db()

        # And one in fifty is voided outright — a walk-out or a mistake. Without
        # them the void report is a screen that has never been true.
        if self.random.random() < 0.02:
            order_services.void_order(order, reason="العميل غادر", actor=cashier)
            return

        method = self.random.choices(
            [methods["CASH"], methods["CARD"], methods["WALLET"]], weights=[65, 30, 5]
        )[0]
        payment_services.take_payment(
            order=order,
            method=method,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
            shift=shift,
            user=cashier,
        )

    def _seat_the_room(self, branch, staff) -> None:
        """
        Leave the floor mid-service: some tables sat, some free, one waiting to
        be cleared. An empty floor map demonstrates nothing about a floor map.
        """
        if TableSession.objects.filter(closed_at__isnull=True).exists():
            return

        tables = list(Table.objects.filter(area__branch=branch).order_by("number"))
        waiters = [staff["waiter@caesar.test"], staff["waiter2@caesar.test"]]

        # Roughly half the room, with parties smaller than the tables they are
        # at — which is what makes "4 seats, 2 seated" worth drawing.
        for index, table in enumerate(tables):
            if index % 2:
                continue
            guests = max(1, self.random.randint(1, table.seats))
            TableSession.objects.create(
                table=table,
                guest_count=guests,
                waiter=waiters[index % 2],
                opened_by=waiters[index % 2],
            )
            Table.objects.filter(pk=table.pk).update(status=TableStatus.OCCUPIED)

        # One table has just been paid and not yet wiped down.
        if tables:
            Table.objects.filter(pk=tables[-1].pk).update(status=TableStatus.CLEANING)

    def _live_kitchen(self, branch, menu, staff) -> None:
        """
        Tickets in progress at every stage, including one that is late.

        A kitchen display with nothing on it demonstrates the empty state and
        nothing else — and the late colouring, which is the whole point of the
        screen, is exactly what an empty board cannot show.
        """
        from apps.kitchen import services as kitchen
        from apps.kitchen.models import KitchenTicket, TicketStatus
        from apps.orders import services as order_services

        if KitchenTicket.objects.filter(branch=branch, status__in=["NEW", "PREPARING"]).exists():
            return

        cashier = staff["cashier@caesar.test"]
        sellable = list(menu.values())
        open_tables = list(
            TableSession.objects.filter(
                closed_at__isnull=True, table__area__branch=branch
            ).select_related("table")[:6]
        )

        for index, session in enumerate(open_tables):
            self.counter += 1
            order = order_services.open_order(
                branch=branch,
                order_type="DINE_IN",
                table_session=session,
                user=cashier,
                local_number=f"MB-01-{self.counter:05d}",
            )
            order_services.apply_events(
                order,
                [
                    {
                        "id": str(uuid.uuid4()),
                        "type": EventType.ITEM_ADDED,
                        "payload": {
                            "line_id": str(uuid.uuid4()),
                            "variant_id": str(self.random.choice(sellable).id),
                            "quantity": "1",
                        },
                    }
                    for _ in range(self.random.randint(1, 3))
                ],
                actor=cashier,
            )
            order.refresh_from_db()
            result = kitchen.route_order(order, user=cashier)

            for ticket in result.tickets:
                # A spread across the states, and one deliberately old so the
                # board has something red on it.
                if index == 0:
                    KitchenTicket.objects.filter(pk=ticket.pk).update(
                        created_at=timezone.now() - timedelta(minutes=26)
                    )
                elif index % 3 == 1:
                    kitchen.transition(ticket, TicketStatus.PREPARING, user=cashier)
                elif index % 3 == 2:
                    kitchen.transition(ticket, TicketStatus.PREPARING, user=cashier)
                    kitchen.transition(ticket, TicketStatus.READY, user=cashier)

    def _children_inside(self, branch, staff) -> None:
        """Children mid-visit, one of them already over their time."""
        from apps.kids import services as kids
        from apps.kids.models import Child, PlaySession, SessionStatus

        area = PlayArea.objects.filter(branch=branch).first()
        if (
            area is None
            or PlaySession.objects.filter(area=area, status=SessionStatus.ACTIVE).exists()
        ):
            return

        tariffs = list(area.tariffs.all())
        children = list(
            Child.objects.filter(guardian__branch=branch).select_related("guardian")[:4]
        )

        for index, child in enumerate(children):
            result = kids.check_in(
                area=area,
                child_name=child.first_name,
                guardian_name=child.guardian.full_name,
                guardian_phone=child.guardian.phone,
                guardian=child.guardian,
                child=child,
                tariff=tariffs[index % len(tariffs)],
                tag_number=str(14 + index),
                medical_notes=child.medical_notes,
                user=staff["kids@caesar.test"],
            )
            # Backdated so the board shows real elapsed time, and the first one
            # is over its included minutes — which is the state the amber and
            # red cards exist for.
            minutes = [95, 40, 20, 8][index]
            PlaySession.objects.filter(pk=result.session.pk).update(
                checked_in_at=timezone.now() - timedelta(minutes=minutes)
            )

    # ── report ───────────────────────────────────────────────────────────────

    def _summary(self, branches, days: int) -> None:
        write = self.stdout.write
        ok = self.style.SUCCESS

        write("")
        write(ok("كافيه القيصر — demo data ready"))
        write("")

        # Per branch, not totalled. A single figure across three branches is the
        # one number that cannot tell you a branch came out empty, which is the
        # failure this seed is most likely to have.
        for branch in branches:
            orders = Order.objects.filter(branch=branch).count()
            write(
                f"  {branch.code:<3} {branch.name_ar:<16} "
                f"{orders:>6} orders  "
                f"{ProductVariant.objects.filter(product__branch=branch).count():>3} menu  "
                f"{InventoryItem.objects.filter(branch=branch).count():>3} stock  "
                f"{Table.objects.filter(area__branch=branch).count():>3} tables  "
                f"{Shift.objects.filter(branch=branch).count():>3} shifts"
            )
        write("")
        write(f"  over {days} trading days, {Order.objects.count()} orders in total")
        write(
            f"  {TableSession.objects.filter(closed_at__isnull=True).count():>6}  "
            "parties seated right now"
        )
        write("")
        write(ok("  Sign in — password for every account:"))
        write(f"      {DEMO_PASSWORD}")
        write("")
        for email, name, role, pin in STAFF:
            write(f"      {email:<26} {name:<18} {role:<18} PIN {pin}")
        write("")

        # Printed here and nowhere else. `issue_license` keeps a hash, so this
        # is the last moment the plaintext exists anywhere — a key that scrolls
        # past unread is a licence nobody can activate against, and the only
        # remedy is for an owner to regenerate it.
        write(ok("  Activate a till — the POS opens for nothing without this:"))
        # One key per branch, because a licence is issued per branch and a till
        # activated against the wrong one is refused. Printing only the last would
        # leave two branches full of data and no way to open a till in either.
        for code, key in self.license_keys.items():
            write(f"      {code:<3} licence key   {key}")
        write("      device name   anything — «كاشير الباب»")
        write("")
        write(
            "      Shown once. Re-run with --reset for a new one; the owner can also\n"
            "      regenerate it from the licensing screen."
        )
        write("")
        write(
            self.style.WARNING(
                "  These are demo credentials, and two-factor is OFF for this organisation\n"
                "  (security.require_mfa_for_roles = []). `bootstrap` is what a real cafe\n"
                "  runs, and it leaves MFA required for SUPER_ADMIN and BRANCH_MANAGER."
            )
        )
        write("")
