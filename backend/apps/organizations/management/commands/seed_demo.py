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
from apps.orders.models import EventType, Order
from apps.organizations.models import Branch, Organization
from apps.payments.models import PaymentMethod
from apps.printing.models import Printer
from apps.purchasing.models import GoodsReceipt, GRLine, PurchaseOrder
from apps.recipes.models import Recipe, RecipeLine
from apps.shifts.models import Shift
from apps.suppliers.models import Supplier, SupplierLedgerEntry

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

        with transaction.atomic():
            org, branch = self._organization()
            self._relax_mfa(org)
            roles = ensure_system_roles(org)
            staff = self._staff(org, branch, roles)
            stations = self._stations(org, branch)
            self._printers(org, branch, stations)
            units = self._units(org)
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

        # Trading runs outside the setup transaction: a fortnight is thousands
        # of rows, and one long transaction holding every lock is how a seed
        # command times out on a machine that is also serving a dev server.
        days = self._trade(branch, menu, methods, staff, options["days"])
        self._seat_the_room(branch, staff)
        self._live_kitchen(branch, menu, staff)
        self._children_inside(branch, staff)

        self._summary(branch, days)

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
        from apps.sync.models import ChangeLog

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

        self.stdout.write(
            f"Reset: removed {removed} demo orders and their trading. "
            f"Kept {kept} audit rows — that log is append-only."
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
        branch, _ = Branch.objects.get_or_create(
            organization=org,
            code="MB",
            defaults={
                "name_ar": "الفرع الرئيسي",
                "name_en": "Main Branch",
                "phone": "0132600000",
                "address": "شارع الصلاحة، آخر شارع قاعة الدار البيضاء، بحري شبين القناطر",
            },
        )
        return org, branch

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

    def _staff(self, org, branch, roles) -> dict[str, User]:
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

            RoleAssignment.objects.get_or_create(
                user=user,
                role=roles[role_code],
                branch=None if role_code == "SUPER_ADMIN" else branch,
            )
            people[email] = user

        return people

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

    def _trade(self, branch, menu, methods, staff, days: int) -> int:
        """
        A fortnight of sales, through the real order and payment services.

        Volume follows the shape of a real cafe day — a morning coffee run, a
        quiet afternoon, a heavy evening — because a flat distribution makes the
        hourly report look broken and hides whether it works.
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
                for _ in range(int(count * busy)):
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
        order_type = self.random.choices(["DINE_IN", "TAKEAWAY", "DELIVERY"], weights=[70, 25, 5])[
            0
        ]

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

    def _summary(self, branch, days: int) -> None:
        write = self.stdout.write
        ok = self.style.SUCCESS

        write("")
        write(ok("كافيه القيصر — demo data ready"))
        write("")
        write(f"  {Order.objects.filter(branch=branch).count():>6}  orders over {days} days")
        write(f"  {Table.objects.filter(area__branch=branch).count():>6}  tables across 2 areas")
        write(
            f"  {TableSession.objects.filter(closed_at__isnull=True).count():>6}  "
            "parties seated right now"
        )
        write(f"  {ProductVariant.objects.filter(product__branch=branch).count():>6}  menu items")
        write(f"  {InventoryItem.objects.filter(branch=branch).count():>6}  stock items")
        write(f"  {Shift.objects.filter(branch=branch).count():>6}  shifts closed")
        write("")
        write(ok("  Sign in — password for every account:"))
        write(f"      {DEMO_PASSWORD}")
        write("")
        for email, name, role, pin in STAFF:
            write(f"      {email:<26} {name:<18} {role:<18} PIN {pin}")
        write("")
        write(
            self.style.WARNING(
                "  These are demo credentials, and two-factor is OFF for this organisation\n"
                "  (security.require_mfa_for_roles = []). `bootstrap` is what a real cafe\n"
                "  runs, and it leaves MFA required for SUPER_ADMIN and BRANCH_MANAGER."
            )
        )
        write("")
