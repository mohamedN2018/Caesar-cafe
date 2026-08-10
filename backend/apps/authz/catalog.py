"""
The permission catalog — every capability the product can grant.

Codes are `domain.action` strings on a custom Role model, NOT Django's
`auth.Permission` (docs/05, commitment C7). Django's model-bound vocabulary
(`add_order`, `change_order`) cannot express what this system actually controls:

    orders.discount            — change an order, but only that field, within a limit
    orders.void_after_fire     — voiding is routine before the kitchen sees it,
                                 and a loss-prevention event afterwards
    shifts.close_with_variance — closing a shift is routine; closing one that is
                                 200 EGP short is not

These are business capabilities, not CRUD verbs on a table.

Codes end up in the audit log, in frontend guards, and in the Desktop's cached
capability set — so renaming one is a migration, not a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    code: str
    group: str
    label_ar: str
    description_ar: str = ""
    sensitive: bool = False
    """Financial or safety impact — always audited, and worth a second look in review."""


def _p(code, group, label_ar, description_ar="", sensitive=False) -> PermissionDef:
    return PermissionDef(code, group, label_ar, description_ar, sensitive)


PERMISSIONS: tuple[PermissionDef, ...] = (
    # ── Orders ───────────────────────────────────────────────────────────────
    _p("orders.view", "orders", "عرض الطلبات"),
    _p("orders.create", "orders", "إنشاء طلب"),
    _p("orders.edit_items", "orders", "تعديل أصناف الطلب"),
    _p("orders.void_item", "orders", "إلغاء صنف", sensitive=True),
    _p(
        "orders.void_after_fire",
        "orders",
        "إلغاء صنف بعد الإرسال للمطبخ",
        "بعد انتهاء مهلة السماح — يُسجَّل دائماً في سجل التدقيق.",
        sensitive=True,
    ),
    _p("orders.void_order", "orders", "إلغاء طلب كامل", sensitive=True),
    _p("orders.discount", "orders", "تطبيق خصم", "ضمن الحد المسموح للدور.", sensitive=True),
    _p("orders.discount_unlimited", "orders", "خصم بدون حد", sensitive=True),
    _p("orders.refund", "orders", "استرجاع", sensitive=True),
    _p("orders.reprint", "orders", "إعادة طباعة الفاتورة"),
    _p("orders.change_price", "orders", "تعديل السعر يدوياً", sensitive=True),
    # ── Payments ─────────────────────────────────────────────────────────────
    _p("payments.take", "payments", "تحصيل الدفع", sensitive=True),
    _p("payments.split", "payments", "تقسيم الدفع"),
    _p("payments.view_all", "payments", "عرض كل المدفوعات"),
    # ── Floor ────────────────────────────────────────────────────────────────
    _p("floor.view", "floor", "عرض الصالة"),
    _p("floor.open_table", "floor", "فتح طاولة"),
    _p("floor.transfer", "floor", "نقل طاولة"),
    _p("floor.merge", "floor", "دمج طاولات"),
    # ── Kitchen ──────────────────────────────────────────────────────────────
    _p("kitchen.view", "kitchen", "عرض شاشة المطبخ"),
    _p("kitchen.update_status", "kitchen", "تحديث حالة التحضير"),
    _p("kitchen.recall", "kitchen", "استرجاع تذكرة"),
    _p("kitchen.manage_stations", "kitchen", "إدارة محطات التحضير"),
    # ── Kids area (docs/12) ──────────────────────────────────────────────────
    _p("kids.view", "kids", "عرض صالة الأطفال"),
    _p("kids.checkin", "kids", "تسجيل دخول طفل"),
    _p("kids.checkout", "kids", "تسجيل خروج طفل", sensitive=True),
    _p(
        "kids.release_to_other",
        "kids",
        "التسليم لغير ولي الأمر المسجّل",
        "إجراء يتعلق بسلامة طفل — يتطلب موافقة مشرف ويُسجَّل الطرفان.",
        sensitive=True,
    ),
    _p("kids.override_charge", "kids", "تعديل قيمة الجلسة", sensitive=True),
    _p("kids.extend_session", "kids", "تمديد جلسة"),
    _p("kids.manage_tariffs", "kids", "إدارة التعريفات", sensitive=True),
    _p("kids.manage_areas", "kids", "إدارة الصالات"),
    _p("kids.log_incident", "kids", "تسجيل حادث"),
    _p("kids.view_reports", "kids", "تقارير صالة الأطفال"),
    # ── Catalog ──────────────────────────────────────────────────────────────
    _p("catalog.view", "catalog", "عرض المنتجات"),
    _p("catalog.create", "catalog", "إضافة منتج"),
    _p("catalog.edit", "catalog", "تعديل منتج"),
    _p("catalog.change_price", "catalog", "تغيير الأسعار", sensitive=True),
    _p("catalog.manage_recipes", "catalog", "إدارة الوصفات"),
    # ── Inventory ────────────────────────────────────────────────────────────
    _p("inventory.view", "inventory", "عرض المخزون"),
    _p("inventory.adjust", "inventory", "تسوية المخزون", sensitive=True),
    _p("inventory.waste", "inventory", "تسجيل هالك", sensitive=True),
    _p("inventory.count", "inventory", "الجرد"),
    _p("inventory.post_count", "inventory", "ترحيل الجرد", sensitive=True),
    # ── Purchasing ───────────────────────────────────────────────────────────
    _p("purchasing.view", "purchasing", "عرض المشتريات"),
    _p("purchasing.create_po", "purchasing", "إنشاء أمر شراء"),
    _p("purchasing.receive", "purchasing", "استلام بضاعة", sensitive=True),
    _p("purchasing.manage_suppliers", "purchasing", "إدارة الموردين"),
    _p("purchasing.pay_supplier", "purchasing", "سداد للمورد", sensitive=True),
    # ── Shifts ───────────────────────────────────────────────────────────────
    _p("shifts.open", "shifts", "فتح وردية"),
    _p("shifts.close", "shifts", "إغلاق وردية", sensitive=True),
    _p("shifts.close_with_variance", "shifts", "إغلاق وردية بفرق نقدي", sensitive=True),
    _p("shifts.cash_movement", "shifts", "حركة نقدية", sensitive=True),
    _p("shifts.view_all", "shifts", "عرض كل الورديات"),
    # ── Reports ──────────────────────────────────────────────────────────────
    _p("reports.sales", "reports", "تقارير المبيعات"),
    _p("reports.products", "reports", "تقارير المنتجات"),
    _p("reports.inventory", "reports", "تقارير المخزون"),
    _p("reports.financial", "reports", "التقارير المالية", sensitive=True),
    _p("reports.employees", "reports", "تقارير الموظفين", sensitive=True),
    _p("reports.export", "reports", "تصدير التقارير"),
    # ── Staff ────────────────────────────────────────────────────────────────
    _p("staff.view", "staff", "عرض الموظفين"),
    _p("staff.manage_users", "staff", "إدارة المستخدمين", sensitive=True),
    _p("staff.manage_roles", "staff", "إدارة الأدوار والصلاحيات", sensitive=True),
    _p("staff.reset_pin", "staff", "إعادة تعيين رمز الدخول", sensitive=True),
    # ── Branch & devices ─────────────────────────────────────────────────────
    _p("branch.view", "branch", "عرض بيانات الفرع"),
    _p("branch.edit_settings", "branch", "تعديل إعدادات الفرع", sensitive=True),
    _p("branch.manage_tables", "branch", "إدارة الطاولات"),
    _p("branch.manage_printers", "branch", "إدارة الطابعات"),
    _p("devices.view", "devices", "عرض الأجهزة"),
    _p("devices.manage", "devices", "إدارة الأجهزة", sensitive=True),
    # ── Licensing ────────────────────────────────────────────────────────────
    _p("licenses.view", "licensing", "عرض التراخيص"),
    _p("licenses.manage", "licensing", "إدارة التراخيص", sensitive=True),
    # ── Sync (docs/07) ───────────────────────────────────────────────────────
    # Push and pull are NOT here on purpose: they are device operations,
    # authorized by the activated terminal itself. Gating them behind a human's
    # permission would mean an outbox that cannot drain at 3am — which is
    # exactly when a terminal that has been queueing since Tuesday needs to.
    _p("sync.view", "sync", "عرض حالة المزامنة"),
    _p("sync.resolve_conflicts", "sync", "حل تعارضات المزامنة", sensitive=True),
    # ── System ───────────────────────────────────────────────────────────────
    _p("system.settings", "system", "إعدادات النظام", sensitive=True),
    _p("audit.view", "system", "عرض سجل التدقيق"),
    _p("backups.manage", "system", "إدارة النسخ الاحتياطي", sensitive=True),
)

PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS)
BY_CODE: dict[str, PermissionDef] = {p.code: p for p in PERMISSIONS}
SENSITIVE_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS if p.sensitive)


def is_valid(code: str) -> bool:
    return code in PERMISSION_CODES


def by_group() -> dict[str, list[PermissionDef]]:
    grouped: dict[str, list[PermissionDef]] = {}
    for permission in PERMISSIONS:
        grouped.setdefault(permission.group, []).append(permission)
    return grouped


# ── System roles ─────────────────────────────────────────────────────────────
# The matrix from docs/05. These ship as is_system=True: their permissions are
# editable, but they cannot be deleted — losing the Cashier role at 8am on a
# Friday is unrecoverable in a way nothing else in the product is.

SYSTEM_ROLES: dict[str, dict] = {
    "SUPER_ADMIN": {
        "name_ar": "مدير عام",
        "permissions": sorted(PERMISSION_CODES),  # everything
    },
    "BRANCH_MANAGER": {
        "name_ar": "مدير فرع",
        # Listed explicitly rather than "everything except N". A subtractive
        # definition silently grants every permission added later — including
        # ones a branch manager should never hold. Note the deliberate absences:
        #   system.settings        → they get branch.edit_settings only, so they
        #                            cannot weaken security.* or licensing
        #   staff.manage_roles     → 🔓 step-up, not held directly
        #   devices.manage         → 🔓 step-up
        #   orders.change_price    → 🔓 step-up
        #   licenses.manage        → Super Admin only
        #   backups.manage         → Super Admin only
        "permissions": [
            "orders.view",
            "orders.create",
            "orders.edit_items",
            "orders.void_item",
            "orders.void_after_fire",
            "orders.void_order",
            "orders.discount",
            "orders.discount_unlimited",
            "orders.refund",
            "orders.reprint",
            "payments.take",
            "payments.split",
            "payments.view_all",
            "floor.view",
            "floor.open_table",
            "floor.transfer",
            "floor.merge",
            "kitchen.view",
            "kitchen.update_status",
            "kitchen.recall",
            "kitchen.manage_stations",
            "kids.view",
            "kids.checkin",
            "kids.checkout",
            "kids.release_to_other",
            "kids.override_charge",
            "kids.extend_session",
            "kids.manage_tariffs",
            "kids.manage_areas",
            "kids.log_incident",
            "kids.view_reports",
            "catalog.view",
            "catalog.create",
            "catalog.edit",
            "catalog.change_price",
            "catalog.manage_recipes",
            "inventory.view",
            "inventory.adjust",
            "inventory.waste",
            "inventory.count",
            "inventory.post_count",
            "purchasing.view",
            "purchasing.create_po",
            "purchasing.receive",
            "purchasing.manage_suppliers",
            "purchasing.pay_supplier",
            "shifts.open",
            "shifts.close",
            "shifts.close_with_variance",
            "shifts.cash_movement",
            "shifts.view_all",
            "reports.sales",
            "reports.products",
            "reports.inventory",
            "reports.financial",
            "reports.employees",
            "reports.export",
            "staff.view",
            "staff.manage_users",
            "staff.reset_pin",
            "branch.view",
            "branch.edit_settings",
            "branch.manage_tables",
            "branch.manage_printers",
            "devices.view",
            "licenses.view",
            "sync.view",
            "sync.resolve_conflicts",
            "audit.view",
        ],
    },
    "CASHIER": {
        "name_ar": "كاشير",
        "permissions": [
            "orders.view",
            "orders.create",
            "orders.edit_items",
            "orders.void_item",
            "orders.discount",
            "orders.reprint",
            "payments.take",
            "payments.split",
            "floor.view",
            "floor.open_table",
            "floor.transfer",
            "floor.merge",
            "kitchen.view",
            "kitchen.update_status",
            "kitchen.recall",
            "kids.view",
            "kids.checkin",
            "kids.checkout",
            "kids.extend_session",
            "kids.log_incident",
            "catalog.view",
            # `inventory.view` accompanies `inventory.waste` deliberately: you
            # cannot sensibly write off an item you are not allowed to look up,
            # and the low-stock alert is exactly what a cashier needs to see.
            "inventory.view",
            "inventory.waste",
            "shifts.open",
            "shifts.close",
            "shifts.cash_movement",
            "reports.sales",
        ],
    },
    "WAITER": {
        "name_ar": "ويتر",
        "permissions": [
            "orders.view",
            "orders.create",
            "orders.edit_items",
            "floor.view",
            "floor.open_table",
            "floor.transfer",
            "kitchen.view",
            "kids.view",
            "catalog.view",
        ],
    },
    "KITCHEN": {
        "name_ar": "مطبخ",
        # No prices, no totals, no financial reports (docs/05 exclusion #3).
        "permissions": [
            "kitchen.view",
            "kitchen.update_status",
            "kitchen.recall",
            "orders.view",
            "catalog.view",
            "inventory.view",
            "inventory.waste",
        ],
    },
    "KIDS_STAFF": {
        "name_ar": "موظف صالة الأطفال",
        # Deliberately narrow: the play area and the incident log, nothing else.
        # Notably NOT kids.override_charge — the person running the area should
        # not be able to alter what a visit costs.
        "permissions": [
            "kids.view",
            "kids.checkin",
            "kids.checkout",
            "kids.extend_session",
            "kids.log_incident",
            "floor.view",
        ],
    },
    "INVENTORY_MANAGER": {
        "name_ar": "أمين مخزن",
        "permissions": [
            "catalog.view",
            "catalog.manage_recipes",
            "inventory.view",
            "inventory.adjust",
            "inventory.waste",
            "inventory.count",
            "inventory.post_count",
            "purchasing.view",
            "purchasing.create_po",
            "purchasing.receive",
            "purchasing.manage_suppliers",
            "reports.products",
            "reports.inventory",
            "reports.export",
        ],
    },
    "ACCOUNTANT": {
        "name_ar": "محاسب",
        # **Sees everything, changes almost nothing.**
        #
        # An accountant reconciling a month needs to open every screen an owner
        # can open — the floor, the kitchen, the kids area, the devices, the
        # sync state — because "why is the 14th short" is not a question that
        # stays inside the finance screens. Withholding those made them ask an
        # owner to read a screen aloud, which is worse for control, not better:
        # the owner ends up sharing a session.
        #
        # So this is EVERY read code in the catalogue, and the write codes are
        # only the two that are genuinely an accountant's job — a supplier
        # record and paying one. Listed explicitly rather than derived from a
        # `.view` suffix: a future permission named `payments.view_all` that
        # happened to grant a write would be swept in silently, and this role's
        # whole value is that it cannot move money out of a till.
        "permissions": [
            # Read across the whole product.
            "orders.view",
            "orders.reprint",
            "payments.view_all",
            "catalog.view",
            "inventory.view",
            "purchasing.view",
            "floor.view",
            "kitchen.view",
            "kids.view",
            "staff.view",
            "devices.view",
            "licenses.view",
            "sync.view",
            "shifts.view_all",
            "branch.view",
            "audit.view",
            # Every report, including the export an accountant lives in.
            "reports.sales",
            "reports.products",
            "reports.inventory",
            "reports.financial",
            "reports.employees",
            "reports.export",
            # The only writes. Both are the finance function itself, and
            # neither can take money out of a drawer or alter a sale.
            "purchasing.manage_suppliers",
            "purchasing.pay_supplier",
        ],
    },
}
