"""
The catalogue of audited actions (docs/09 — "Audited Actions").

Codes live here rather than being typed as string literals at each call site, so
that:

  * `tests/test_audit.py` can assert every catalogued action is actually
    produced by some code path. A table in a document that nothing enforces
    drifts from reality within a month.
  * renaming an action is one edit, not a grep.
  * an unknown code is rejected at write time. An audit trail with a typo in the
    action name is an audit trail with a hole in it, and the hole is invisible
    until someone searches for the thing that is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    """Routine but worth finding later — a discount, a cash movement."""
    WARNING = "WARNING"
    """A loss-prevention or security signal — a void after fire, a lockout."""


@dataclass(frozen=True)
class ActionDef:
    code: str
    domain: str
    label_ar: str
    severity: str = Severity.INFO


def _a(code: str, domain: str, label_ar: str, severity: str = Severity.INFO) -> ActionDef:
    return ActionDef(code=code, domain=domain, label_ar=label_ar, severity=severity)


ACTIONS: tuple[ActionDef, ...] = (
    # ── Orders ───────────────────────────────────────────────────────────────
    _a("order.item_voided", "orders", "إلغاء صنف", Severity.NOTICE),
    _a("order.voided", "orders", "إلغاء طلب", Severity.WARNING),
    _a("order.discount_applied", "orders", "تطبيق خصم", Severity.NOTICE),
    # WARNING, a step above a discount. A discount is bounded by a percentage
    # ceiling somebody set; an override is any number at all, which makes it the
    # shorter path from the till to the drawer.
    _a("order.price_overridden", "orders", "تعديل سعر يدوياً", Severity.WARNING),
    _a("order.reopen_attempt", "orders", "محاولة إعادة فتح طلب مدفوع", Severity.WARNING),
    # ── Payments ─────────────────────────────────────────────────────────────
    _a("order.receipt_reprinted", "orders", "إعادة طباعة فاتورة", Severity.NOTICE),
    # Merging moves orders between records — one bill where there were two.
    _a("floor.sessions_merged", "orders", "دمج طاولتين", Severity.NOTICE),
    _a("payment.taken", "payments", "تحصيل دفعة"),
    _a("payment.refunded", "payments", "استرجاع مبلغ", Severity.WARNING),
    # ── Catalog ──────────────────────────────────────────────────────────────
    _a("catalog.price_changed", "catalog", "تغيير سعر", Severity.NOTICE),
    _a("catalog.product_deactivated", "catalog", "إيقاف منتج", Severity.NOTICE),
    _a("catalog.recipe_changed", "catalog", "تعديل وصفة", Severity.NOTICE),
    # ── Inventory ────────────────────────────────────────────────────────────
    _a("inventory.adjusted", "inventory", "تسوية مخزون", Severity.WARNING),
    _a("inventory.waste_recorded", "inventory", "تسجيل هالك", Severity.NOTICE),
    _a("inventory.count_posted", "inventory", "ترحيل جرد", Severity.WARNING),
    # ── Purchasing ───────────────────────────────────────────────────────────
    _a("purchasing.po_approved", "purchasing", "اعتماد أمر شراء", Severity.NOTICE),
    _a("purchasing.goods_received", "purchasing", "استلام بضاعة", Severity.NOTICE),
    _a("purchasing.supplier_paid", "purchasing", "سداد لمورد", Severity.WARNING),
    # ── Shifts ───────────────────────────────────────────────────────────────
    _a("shift.opened", "shifts", "فتح وردية"),
    _a("shift.closed", "shifts", "إغلاق وردية", Severity.NOTICE),
    _a("shift.variance_recorded", "shifts", "فرق نقدي", Severity.WARNING),
    _a("shift.cash_movement", "shifts", "حركة نقدية", Severity.NOTICE),
    # ── Staff ────────────────────────────────────────────────────────────────
    _a("staff.user_created", "staff", "إنشاء مستخدم", Severity.NOTICE),
    _a("staff.role_changed", "staff", "تغيير دور", Severity.WARNING),
    _a("staff.pin_reset", "staff", "إعادة تعيين رمز الدخول", Severity.WARNING),
    _a("staff.user_deactivated", "staff", "إيقاف مستخدم", Severity.WARNING),
    # ── Licensing ────────────────────────────────────────────────────────────
    _a("license.created", "licensing", "إنشاء ترخيص", Severity.NOTICE),
    _a("license.activated", "licensing", "تفعيل جهاز", Severity.NOTICE),
    _a("license.suspended", "licensing", "إيقاف ترخيص", Severity.WARNING),
    _a("license.revoked", "licensing", "إلغاء ترخيص", Severity.WARNING),
    _a("license.renewed", "licensing", "تجديد ترخيص", Severity.NOTICE),
    _a("device.reset", "licensing", "إعادة تعيين جهاز", Severity.WARNING),
    # ── Kids ─────────────────────────────────────────────────────────────────
    # Not in the docs/09 table, added because a child released to someone other
    # than the registering guardian is the one event in this product whose
    # failure is not financial.
    _a("kids.released_to_other", "kids", "تسليم طفل لغير ولي الأمر", Severity.WARNING),
    _a("kids.charge_overridden", "kids", "تعديل قيمة جلسة", Severity.WARNING),
    # ── System ───────────────────────────────────────────────────────────────
    _a("system.setting_changed", "system", "تغيير إعداد", Severity.NOTICE),
    _a("system.backup_triggered", "system", "تشغيل نسخة احتياطية", Severity.NOTICE),
    _a("system.restore_performed", "system", "استعادة نسخة احتياطية", Severity.WARNING),
    _a("sync.conflict_resolved", "system", "حل تعارض مزامنة", Severity.NOTICE),
    # ── Auth ─────────────────────────────────────────────────────────────────
    _a("auth.login_failed", "auth", "فشل تسجيل دخول متكرر", Severity.WARNING),
    _a("auth.lockout", "auth", "قفل حساب", Severity.WARNING),
    _a("auth.refresh_reuse_detected", "auth", "إعادة استخدام رمز تحديث", Severity.WARNING),
    _a("auth.mfa_enrolled", "auth", "تسجيل المصادقة الثنائية"),
    _a("auth.step_up_approved", "auth", "موافقة مشرف", Severity.NOTICE),
)

BY_CODE: dict[str, ActionDef] = {action.code: action for action in ACTIONS}
CODES: frozenset[str] = frozenset(BY_CODE)
DOMAINS: tuple[str, ...] = tuple(dict.fromkeys(action.domain for action in ACTIONS))


def is_valid(code: str) -> bool:
    return code in CODES


def severity_of(code: str) -> str:
    action = BY_CODE.get(code)
    return action.severity if action else Severity.INFO


def by_domain() -> dict[str, list[ActionDef]]:
    grouped: dict[str, list[ActionDef]] = {}
    for action in ACTIONS:
        grouped.setdefault(action.domain, []).append(action)
    return grouped
