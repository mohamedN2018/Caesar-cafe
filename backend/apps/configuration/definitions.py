"""
Registered settings.

This file IS the catalog in docs/11-configuration.md. Phase 1 registers the
organization, finance, floor and security groups — the ones the answered
architecture questions depend on. Later phases append their own groups here as
each domain lands; the shape never changes.

Adding a setting: one `register(...)` call. No migration, no API change, no
frontend work.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from .registry import Range, Scope, SettingType, SubsetOf, register

ORDER_TYPES = ("DINE_IN", "TAKE_AWAY", "DELIVERY")
WEEKDAYS = (
    "SATURDAY",
    "SUNDAY",
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
)

# ── Organization & locale ────────────────────────────────────────────────────

register(
    key="org.currency",
    type=SettingType.STRING,
    default="EGP",
    scope=Scope.ORGANIZATION,
    group="organization",
    label_ar="العملة",
    label_en="Currency",
    pushes_to_desktop=True,
)
register(
    key="org.currency_decimals",
    type=SettingType.INTEGER,
    default=2,
    scope=Scope.ORGANIZATION,
    group="organization",
    label_ar="عدد الخانات العشرية",
    label_en="Currency decimals",
    validators=(Range(0, 3),),
    pushes_to_desktop=True,
)
register(
    key="org.timezone",
    type=SettingType.STRING,
    default="Africa/Cairo",
    scope=Scope.ORGANIZATION,
    group="organization",
    label_ar="المنطقة الزمنية",
    label_en="Timezone",
    help_ar="كل الأوقات تُخزّن بتوقيت UTC وتُعرض بهذه المنطقة.",
    pushes_to_desktop=True,
)
register(
    key="org.default_language",
    type=SettingType.ENUM,
    default="ar",
    scope=Scope.ORGANIZATION,
    group="organization",
    label_ar="اللغة الافتراضية",
    label_en="Default language",
    choices=("ar", "en"),
    pushes_to_desktop=True,
)
register(
    key="org.numeral_system",
    type=SettingType.ENUM,
    default="western",
    scope=Scope.ORGANIZATION,
    group="organization",
    label_ar="نظام الأرقام",
    label_en="Numeral system",
    help_ar="western = ٠١٢ بالشكل 012 · eastern = ٠١٢",
    choices=("western", "eastern"),
    pushes_to_desktop=True,
)

# ── Finance ──────────────────────────────────────────────────────────────────

register(
    key="finance.vat_enabled",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="finance",
    label_ar="تفعيل ضريبة القيمة المضافة",
    label_en="VAT enabled",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
    affects_open_orders=True,
)
register(
    key="finance.vat_percent",
    type=SettingType.DECIMAL,
    default=Decimal("14.00"),
    scope=Scope.BRANCH,
    group="finance",
    label_ar="نسبة ض.ق.م",
    label_en="VAT percent",
    help_ar="تسري على الطلبات الجديدة فقط. الطلبات المفتوحة تحتفظ بالنسبة وقت فتحها.",
    validators=(Range(Decimal("0"), Decimal("100")),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
    affects_open_orders=True,
)
register(
    key="finance.vat_inclusive",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="finance",
    label_ar="الأسعار شاملة الضريبة",
    label_en="Prices include VAT",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
    affects_open_orders=True,
)
register(
    key="finance.service_enabled",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="finance",
    label_ar="تفعيل رسوم الخدمة",
    label_en="Service charge enabled",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
    affects_open_orders=True,
)
register(
    key="finance.service_percent",
    type=SettingType.DECIMAL,
    default=Decimal("12.00"),
    scope=Scope.BRANCH,
    group="finance",
    label_ar="نسبة الخدمة",
    label_en="Service percent",
    validators=(Range(Decimal("0"), Decimal("100")),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
    affects_open_orders=True,
)
register(
    key="finance.service_applies_to",
    type=SettingType.LIST,
    default=["DINE_IN"],
    scope=Scope.BRANCH,
    group="finance",
    label_ar="تطبق الخدمة على",
    label_en="Service applies to",
    validators=(SubsetOf(ORDER_TYPES),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="finance.rounding_step",
    type=SettingType.DECIMAL,
    default=Decimal("0.01"),
    scope=Scope.BRANCH,
    group="finance",
    label_ar="تقريب الإجمالي",
    label_en="Total rounding step",
    help_ar="مثال: ٠.٢٥ يقرّب كل إجمالي لأقرب ربع جنيه. الفرق يظهر في الفاتورة.",
    validators=(Range(Decimal("0.01"), Decimal("5")),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="finance.business_day_start",
    type=SettingType.TIME,
    default=time(4, 0),
    scope=Scope.BRANCH,
    group="finance",
    label_ar="بداية اليوم المحاسبي",
    label_en="Business day start",
    help_ar="الطلبات قبل هذا الوقت تُحسب على اليوم السابق في كل التقارير.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="finance.week_start_day",
    type=SettingType.ENUM,
    default="SATURDAY",
    scope=Scope.BRANCH,
    group="finance",
    label_ar="بداية الأسبوع",
    label_en="Week starts on",
    choices=WEEKDAYS,
    permission="branch.edit_settings",
)

# ── Floor & service model ────────────────────────────────────────────────────

register(
    key="floor.service_mode",
    type=SettingType.ENUM,
    default="WAITER_TERMINAL",
    scope=Scope.BRANCH,
    group="floor",
    label_ar="نظام الخدمة",
    label_en="Service mode",
    help_ar=(
        "CASHIER_ONLY: الكاشير يدخل كل الطلبات · "
        "WAITER_TERMINAL: أجهزة مشتركة للويترز · "
        "WAITER_DEVICE: كل ويتر معه جهاز"
    ),
    choices=("CASHIER_ONLY", "WAITER_TERMINAL", "WAITER_DEVICE"),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="floor.waiter_can_fire_to_kitchen",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="floor",
    label_ar="الويتر يرسل للمطبخ",
    label_en="Waiter can fire to kitchen",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="floor.waiter_can_take_payment",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="floor",
    label_ar="الويتر يقبض",
    label_en="Waiter can take payment",
    help_ar="مطلوب أيضاً صلاحية payments.take للمستخدم نفسه.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="floor.waiter_sees_only_own_tables",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="floor",
    label_ar="الويتر يرى طاولاته فقط",
    label_en="Waiter sees only own tables",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="floor.waiter_can_apply_discount",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="floor",
    label_ar="الويتر يطبق خصم",
    label_en="Waiter can apply discount",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="floor.auto_cleaning_status",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="floor",
    label_ar="تحويل الطاولة لـ«تنظيف» بعد الإغلاق",
    label_en="Auto cleaning status",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="floor.cleaning_duration_minutes",
    type=SettingType.INTEGER,
    default=5,
    scope=Scope.BRANCH,
    group="floor",
    label_ar="مدة التنظيف (دقائق)",
    label_en="Cleaning duration",
    validators=(Range(0, 120),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)

# ── Orders ───────────────────────────────────────────────────────────────────

register(
    key="orders.default_type",
    type=SettingType.ENUM,
    default="DINE_IN",
    scope=Scope.BRANCH,
    group="orders",
    label_ar="نوع الطلب الافتراضي",
    label_en="Default order type",
    choices=ORDER_TYPES,
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="orders.enabled_types",
    type=SettingType.LIST,
    default=["DINE_IN", "TAKE_AWAY"],
    scope=Scope.BRANCH,
    group="orders",
    label_ar="أنواع الطلبات المفعّلة",
    label_en="Enabled order types",
    validators=(SubsetOf(ORDER_TYPES),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="orders.void_grace_seconds",
    type=SettingType.INTEGER,
    default=120,
    scope=Scope.BRANCH,
    group="orders",
    label_ar="مهلة الإلغاء بعد الإرسال للمطبخ (ثانية)",
    label_en="Void grace after firing",
    help_ar="بعد هذه المهلة يحتاج الإلغاء موافقة مدير ويُسجَّل في سجل التدقيق.",
    validators=(Range(0, 3600),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="orders.require_void_reason",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="orders",
    label_ar="إلزام سبب الإلغاء",
    label_en="Require void reason",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="orders.void_reasons",
    type=SettingType.LIST,
    default=["خطأ في الإدخال", "طلب العميل", "صنف غير متاح", "تأخر التحضير", "أخرى"],
    scope=Scope.BRANCH,
    group="orders",
    label_ar="أسباب الإلغاء",
    label_en="Void reasons",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="orders.allow_price_override",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="orders",
    label_ar="السماح بتعديل السعر يدوياً",
    label_en="Allow manual price override",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)

# ── Discounts ────────────────────────────────────────────────────────────────

register(
    key="discounts.max_percent",
    type=SettingType.DECIMAL,
    default=Decimal("10.00"),
    scope=Scope.ROLE,
    group="discounts",
    label_ar="أقصى نسبة خصم",
    label_en="Max discount percent",
    validators=(Range(Decimal("0"), Decimal("100")),),
    permission="staff.manage_roles",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="discounts.require_reason",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="discounts",
    label_ar="إلزام سبب الخصم",
    label_en="Require discount reason",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="discounts.reasons",
    type=SettingType.LIST,
    default=["عميل دائم", "تعويض عن تأخير", "عرض ترويجي", "موظف", "أخرى"],
    scope=Scope.BRANCH,
    group="discounts",
    label_ar="أسباب الخصم",
    label_en="Discount reasons",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)

# ── Kitchen ──────────────────────────────────────────────────────────────────

register(
    key="kitchen.allow_recall_minutes",
    type=SettingType.INTEGER,
    default=30,
    scope=Scope.BRANCH,
    group="kitchen",
    label_ar="مهلة استرجاع التذكرة (دقيقة)",
    label_en="Recall window",
    help_ar="بعدها لا يمكن استرجاع تذكرة — حتى لا تُستخدم لإعادة كتابة يوم مضى.",
    validators=(Range(0, 1440),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="kitchen.warning_threshold_percent",
    type=SettingType.INTEGER,
    default=80,
    scope=Scope.BRANCH,
    group="kitchen",
    label_ar="نسبة التحذير من الوقت المستهدف",
    label_en="Warning threshold",
    help_ar="عندها تتحول بطاقة التذكرة للون الكهرماني.",
    validators=(Range(10, 100),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kitchen.print_ticket_mode",
    type=SettingType.ENUM,
    default="on_kds_failure",
    scope=Scope.BRANCH,
    group="kitchen",
    label_ar="طباعة تذكرة المطبخ",
    label_en="Print kitchen ticket",
    help_ar=(
        "on_kds_failure: الطباعة هي مسار الطوارئ لو الشاشة أو الشبكة وقعت — "
        "ولهذا طابعة المطبخ ليست اختيارية حتى مع وجود شاشة."
    ),
    choices=("always", "on_kds_failure", "never"),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kitchen.kds_columns",
    type=SettingType.INTEGER,
    default=4,
    scope=Scope.BRANCH,
    group="kitchen",
    label_ar="عدد أعمدة شاشة المطبخ",
    label_en="KDS columns",
    validators=(Range(1, 8),),
    overridable_at=(Scope.DEVICE,),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kitchen.kds_sound_on_new",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kitchen",
    label_ar="صوت تنبيه عند تذكرة جديدة",
    label_en="Sound on new ticket",
    overridable_at=(Scope.DEVICE,),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kitchen.show_prices_on_ticket",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="kitchen",
    label_ar="إظهار الأسعار على تذكرة المطبخ",
    label_en="Show prices on ticket",
    help_ar="مغلق افتراضياً: المطبخ لا يحتاج الأسعار، والأرقام المالية تنتشر بلا سبب.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)

# ── Inventory ────────────────────────────────────────────────────────────────

register(
    key="inventory.deduct_on",
    type=SettingType.ENUM,
    default="PAYMENT",
    scope=Scope.BRANCH,
    group="inventory",
    label_ar="خصم المخزون عند",
    label_en="Deduct stock on",
    help_ar=(
        "PAYMENT أأمن: طلب متروك لا يستهلك مخزون. "
        "FIRE أدق لحظياً: الباريستا استخدم البن سواء دفع العميل أو لا. "
        "أيهما صحيح يعتمد على طريقة عمل الكافيه."
    ),
    choices=("FIRE", "PAYMENT", "SERVE"),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="inventory.allow_negative",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="inventory",
    label_ar="السماح برصيد سالب",
    label_en="Allow negative stock",
    help_ar="كافيه خلص منه صنف وسط الخدمة لازم يقدر يسجل البيع.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="inventory.costing_method",
    type=SettingType.ENUM,
    default="WEIGHTED_AVG",
    scope=Scope.BRANCH,
    group="inventory",
    label_ar="طريقة التكلفة",
    label_en="Costing method",
    choices=("WEIGHTED_AVG", "FIFO"),
    permission="branch.edit_settings",
    high_impact=True,
)
register(
    key="inventory.waste_reasons",
    type=SettingType.LIST,
    default=["انسكاب", "انتهاء صلاحية", "خطأ تحضير", "تلف", "أخرى"],
    scope=Scope.BRANCH,
    group="inventory",
    label_ar="أسباب الهالك",
    label_en="Waste reasons",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="inventory.require_waste_reason",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="inventory",
    label_ar="إلزام سبب الهالك",
    label_en="Require waste reason",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="inventory.count_requires_approval",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="inventory",
    label_ar="ترحيل الجرد يتطلب موافقة",
    label_en="Count requires approval",
    permission="branch.edit_settings",
    high_impact=True,
)

# ── Shifts ───────────────────────────────────────────────────────────────────

register(
    key="shifts.required_to_sell",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="shifts",
    label_ar="إلزام فتح وردية قبل البيع",
    label_en="Shift required to sell",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="shifts.blind_close",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="shifts",
    label_ar="إغلاق أعمى (إخفاء المتوقع)",
    label_en="Blind close",
    help_ar=("الكاشير يعد الدرج دون رؤية المتوقع، فيكون العد ملاحظة حقيقية لا رقماً مشتقاً من هدف."),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="shifts.max_variance",
    type=SettingType.DECIMAL,
    default=Decimal("50.00"),
    scope=Scope.BRANCH,
    group="shifts",
    label_ar="أقصى فرق نقدي بدون موافقة",
    label_en="Max variance without approval",
    validators=(Range(Decimal("0"), Decimal("100000")),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="shifts.require_variance_reason",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="shifts",
    label_ar="إلزام سبب الفرق",
    label_en="Require variance reason",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="shifts.cash_movement_reasons",
    type=SettingType.LIST,
    default=["شراء مستلزمات", "سلفة", "إيداع بنكي", "مصروف نثري", "أخرى"],
    scope=Scope.BRANCH,
    group="shifts",
    label_ar="أسباب الحركة النقدية",
    label_en="Cash movement reasons",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="shifts.max_duration_hours",
    type=SettingType.INTEGER,
    default=16,
    scope=Scope.BRANCH,
    group="shifts",
    label_ar="أقصى مدة وردية (ساعة)",
    label_en="Max shift duration",
    validators=(Range(1, 48),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)

# ── Payments ─────────────────────────────────────────────────────────────────

register(
    key="payments.allow_split",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="payments",
    label_ar="السماح بتقسيم الدفع",
    label_en="Allow split payment",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="payments.allow_partial",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="payments",
    label_ar="السماح بالدفع الجزئي",
    label_en="Allow partial payment",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="payments.quick_tender_mode",
    type=SettingType.ENUM,
    default="smart",
    scope=Scope.BRANCH,
    group="payments",
    label_ar="أزرار المبلغ السريع",
    label_en="Quick tender mode",
    help_ar="smart يحسب أقرب ٥٠ و١٠٠ والمضبوط — أكثر تفاعل في كافيه نقدي.",
    choices=("smart", "fixed", "off"),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)

# ── Security ─────────────────────────────────────────────────────────────────
# The system is internet-facing from Phase 1 (C11), so these defaults are the
# posture the first deployment actually runs with.

register(
    key="security.pin_length",
    type=SettingType.INTEGER,
    default=4,
    scope=Scope.ORGANIZATION,
    group="security",
    label_ar="طول رمز الدخول السريع",
    label_en="POS PIN length",
    validators=(Range(4, 6),),
    high_impact=True,
)
register(
    key="security.pin_lockout_attempts",
    type=SettingType.INTEGER,
    default=5,
    scope=Scope.ORGANIZATION,
    group="security",
    label_ar="عدد المحاولات قبل الإيقاف",
    label_en="PIN attempts before lockout",
    validators=(Range(3, 10),),
    high_impact=True,
)
register(
    key="security.pin_lockout_minutes",
    type=SettingType.INTEGER,
    default=15,
    scope=Scope.ORGANIZATION,
    group="security",
    label_ar="مدة الإيقاف (دقائق)",
    label_en="Lockout duration",
    validators=(Range(1, 240),),
    high_impact=True,
)
register(
    key="security.require_mfa_for_roles",
    type=SettingType.LIST,
    default=["SUPER_ADMIN", "BRANCH_MANAGER"],
    scope=Scope.ORGANIZATION,
    group="security",
    label_ar="إلزام التحقق بخطوتين للأدوار",
    label_en="Require MFA for roles",
    help_ar="هذه الحسابات متاحة من الإنترنت، والباسورد وحده غير كافٍ لها.",
    high_impact=True,
)
register(
    key="security.admin_ip_allowlist",
    type=SettingType.LIST,
    default=[],
    scope=Scope.ORGANIZATION,
    group="security",
    label_ar="عناوين IP المسموح لها بالإدارة",
    label_en="Admin IP allowlist",
    help_ar="فارغة = أي عنوان. اضبطها فقط إذا كان لديك IP ثابت.",
    high_impact=True,
)
register(
    key="security.approval_token_seconds",
    type=SettingType.INTEGER,
    default=60,
    scope=Scope.ORGANIZATION,
    group="security",
    label_ar="صلاحية رمز موافقة المدير (ثانية)",
    label_en="Step-up approval TTL",
    validators=(Range(15, 600),),
    high_impact=True,
)

# ── Sync ─────────────────────────────────────────────────────────────────────

register(
    key="sync.push_interval_seconds",
    type=SettingType.INTEGER,
    default=2,
    scope=Scope.BRANCH,
    group="sync",
    label_ar="فترة الإرسال (ثانية)",
    label_en="Push interval",
    validators=(Range(1, 300),),
    overridable_at=(Scope.DEVICE,),
    pushes_to_desktop=True,
)
register(
    key="sync.push_batch_size",
    type=SettingType.INTEGER,
    default=50,
    scope=Scope.BRANCH,
    group="sync",
    label_ar="حجم دفعة الإرسال",
    label_en="Push batch size",
    help_ar="يمكن ضبط قيمة أقل لجهاز معيّن على اتصال ضعيف.",
    validators=(Range(1, 500),),
    overridable_at=(Scope.DEVICE,),
    pushes_to_desktop=True,
)
register(
    key="sync.offline_alert_minutes",
    type=SettingType.INTEGER,
    default=30,
    scope=Scope.BRANCH,
    group="sync",
    label_ar="تنبيه انقطاع الجهاز (دقائق)",
    label_en="Device offline alert",
    validators=(Range(5, 1440),),
)
register(
    key="sync.pull_page_size",
    type=SettingType.INTEGER,
    default=500,
    scope=Scope.BRANCH,
    group="sync",
    label_ar="حجم صفحة الاستلام",
    label_en="Pull page size",
    validators=(Range(10, 2000),),
    overridable_at=(Scope.DEVICE,),
    pushes_to_desktop=True,
)
register(
    key="sync.max_clock_skew_seconds",
    type=SettingType.INTEGER,
    default=300,
    scope=Scope.BRANCH,
    group="sync",
    label_ar="أقصى فرق مسموح في ساعة الجهاز (ثانية)",
    label_en="Max clock skew",
    help_ar=(
        "تجاوز الحد يُسجَّل كتعارض للمراجعة — ولا تُرفض العملية أبداً بسببه، "
        "لأن البيع حدث فعلاً وساعة الخادم هي المعتمدة في كل ما يهم."
    ),
    validators=(Range(30, 86400),),
    pushes_to_desktop=True,
)
register(
    key="sync.pending_alert_threshold",
    type=SettingType.INTEGER,
    default=100,
    scope=Scope.BRANCH,
    group="sync",
    label_ar="تنبيه عند تراكم العمليات",
    label_en="Pending operations alert",
    help_ar="طابور يكبر معناه جهاز توقف عن الإرسال — والمبيعات على قرص صلب.",
    validators=(Range(1, 10000),),
)

# ── Licensing ────────────────────────────────────────────────────────────────

register(
    key="license.offline_grace_hours",
    type=SettingType.INTEGER,
    default=72,
    scope=Scope.ORGANIZATION,
    group="licensing",
    label_ar="مهلة العمل بدون إنترنت (ساعة)",
    label_en="Offline grace hours",
    validators=(Range(1, 720),),
    permission="licenses.manage",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="license.warn_before_expiry_days",
    type=SettingType.INTEGER,
    default=14,
    scope=Scope.ORGANIZATION,
    group="licensing",
    label_ar="التنبيه قبل انتهاء الترخيص (يوم)",
    label_en="Warn before expiry",
    validators=(Range(1, 180),),
    permission="licenses.manage",
    pushes_to_desktop=True,
)
register(
    key="license.grace_days_after_expiry",
    type=SettingType.INTEGER,
    default=7,
    scope=Scope.ORGANIZATION,
    group="licensing",
    label_ar="مهلة السماح بعد الانتهاء (يوم)",
    label_en="Grace days after expiry",
    help_ar="خلال هذه المدة يعمل النظام بالكامل مع تنبيه دائم.",
    validators=(Range(0, 90),),
    permission="licenses.manage",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="license.heartbeat_interval_minutes",
    type=SettingType.INTEGER,
    default=15,
    scope=Scope.ORGANIZATION,
    group="licensing",
    label_ar="فترة نبضة الجهاز (دقيقة)",
    label_en="Heartbeat interval",
    validators=(Range(1, 1440),),
    permission="licenses.manage",
    pushes_to_desktop=True,
)
register(
    key="license.expiry_policy",
    type=SettingType.ENUM,
    default="BLOCK_NEW_ORDERS",
    scope=Scope.ORGANIZATION,
    group="licensing",
    label_ar="سلوك انتهاء الترخيص",
    label_en="Expiry policy",
    help_ar="لا يتم إيقاف النظام فجأة في كل الأحوال — الطلبات المفتوحة يمكن إنهاؤها دائماً.",
    choices=("READ_ONLY", "GRACE_ONLY", "BLOCK_NEW_ORDERS"),
    permission="licenses.manage",
    pushes_to_desktop=True,
    high_impact=True,
)

# ── Kids area (docs/12) ──────────────────────────────────────────────────────

register(
    key="kids.enabled",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تفعيل صالة الأطفال",
    label_en="Kids area enabled",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.max_capacity",
    type=SettingType.INTEGER,
    default=25,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="أقصى عدد أطفال",
    label_en="Max capacity",
    help_ar="حد أمان — لا يمكن تجاوزه حتى في وضع عدم الاتصال.",
    validators=(Range(1, 500),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="kids.min_age_months",
    type=SettingType.INTEGER,
    default=12,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="أقل سن (بالشهور)",
    label_en="Min age (months)",
    validators=(Range(0, 240),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.max_age_months",
    type=SettingType.INTEGER,
    default=144,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="أقصى سن (بالشهور)",
    label_en="Max age (months)",
    validators=(Range(0, 240),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.enforce_age_limits",
    type=SettingType.ENUM,
    default="warn",
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تطبيق حدود السن",
    label_en="Enforce age limits",
    help_ar="الموظف يرى الطفل؛ النظام يعمل برقم قاله ولي الأمر. لذلك التحذير هو الافتراضي.",
    choices=("off", "warn", "block"),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.require_guardian_verification",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="إلزام التحقق من ولي الأمر عند الخروج",
    label_en="Require guardian verification",
    help_ar="لا يمكن إتمام الخروج بدون تأكيد هوية المستلم.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="kids.release_to_other_requires_approval",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تسليم لغير ولي الأمر يتطلب موافقة مشرف",
    label_en="Release to other requires approval",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="kids.capture_child_photo",
    type=SettingType.BOOLEAN,
    default=False,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تصوير الطفل عند الدخول",
    label_en="Capture child photo",
    help_ar=(
        "مغلق افتراضياً: صور الأطفال بيانات شخصية حساسة، ورقم التاج وهاتف ولي الأمر كافيان للتعريف."
    ),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="kids.grace_minutes",
    type=SettingType.INTEGER,
    default=5,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="مهلة السماح قبل احتساب فترة جديدة (دقائق)",
    label_en="Grace minutes",
    help_ar="يمنع الخلاف اليومي على تأخير دقيقتين.",
    validators=(Range(0, 60),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.rounding",
    type=SettingType.ENUM,
    default="up_to_block",
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تقريب وقت اللعب",
    label_en="Time rounding",
    choices=("up_to_block", "nearest_block", "exact_minutes"),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
register(
    key="kids.warn_before_end_minutes",
    type=SettingType.INTEGER,
    default=10,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تنبيه قبل انتهاء الباقة (دقائق)",
    label_en="Warn before end",
    validators=(Range(0, 60),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.max_session_hours",
    type=SettingType.INTEGER,
    default=6,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="أقصى مدة جلسة (ساعات)",
    label_en="Max session hours",
    help_ar="تنبيه فقط — النظام لا يُخرج طفلاً تلقائياً أبداً.",
    validators=(Range(1, 24),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.require_socks",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="إلزام شراب الأطفال",
    label_en="Require socks",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.auto_link_to_table",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="ربط تلقائي بطاولة ولي الأمر",
    label_en="Auto-link to guardian's table",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.billing_product",
    type=SettingType.STRING,
    default="",
    scope=Scope.BRANCH,
    group="kids",
    label_ar="صنف احتساب جلسة اللعب",
    label_en="Play session billing product",
    help_ar=(
        "الصنف الذي تُسجَّل عليه الجلسة في الفاتورة. السعر يأتي من التعريفة وليس من الصنف — "
        "وجوده فقط ليظهر دخل الصالة في تقارير المبيعات مع باقي الأصناف."
    ),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.socks_product",
    type=SettingType.STRING,
    default="",
    scope=Scope.BRANCH,
    group="kids",
    label_ar="صنف شراب الأطفال",
    label_en="Socks product",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.default_tariff",
    type=SettingType.STRING,
    default="",
    scope=Scope.BRANCH,
    group="kids",
    label_ar="التعريفة الافتراضية",
    label_en="Default tariff",
    help_ar="تُقترح عند الدخول إذا لم تطابق أي تعريفة موقوتة وقت الدخول.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.require_guardian_phone",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="إلزام رقم هاتف ولي الأمر",
    label_en="Require guardian phone",
    help_ar="الهاتف هو وسيلة الوصول الوحيدة لولي الأمر إذا حدث شيء.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.overdue_alert_minutes",
    type=SettingType.INTEGER,
    default=5,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="تنبيه التأخير بعد انتهاء الباقة (دقائق)",
    label_en="Overdue alert",
    validators=(Range(0, 120),),
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.print_checkin_slip",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="طباعة إيصال الدخول",
    label_en="Print check-in slip",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.tag_numbers",
    type=SettingType.STRING,
    default="1-30",
    scope=Scope.BRANCH,
    group="kids",
    label_ar="أرقام التاجات المتاحة",
    label_en="Wristband tag range",
    help_ar="مدى مثل 1-30، أو أرقام مفصولة بفواصل.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
)
register(
    key="kids.allow_charge_override",
    type=SettingType.BOOLEAN,
    default=True,
    scope=Scope.BRANCH,
    group="kids",
    label_ar="السماح بتعديل قيمة الجلسة",
    label_en="Allow charge override",
    help_ar="مقيَّد بصلاحية kids.override_charge، والقيمة المحسوبة تُحفظ دائماً بجوار المعدَّلة.",
    permission="branch.edit_settings",
    pushes_to_desktop=True,
    high_impact=True,
)
