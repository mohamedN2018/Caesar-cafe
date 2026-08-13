"""
What a mirror row looks like on the device.

Deliberately hand-written rather than generic model serialization. A mirror
payload is a wire format that a shipped Desktop parses: adding a field to a
Django model must not silently change what a terminal in the field receives, and
removing one must be a visible edit here rather than a surprise at 8pm on a
Friday.

Every payload is JSON-safe — Decimals become strings, for the same reason money
crosses the API as a string.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _d(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _t(value) -> str | None:
    return None if value is None else value.isoformat()


def category(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "parent_id": str(obj.parent_id) if obj.parent_id else None,
        "name_ar": obj.name_ar,
        "name_en": obj.name_en,
        "color": obj.color,
        "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def product(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "category_id": str(obj.category_id),
        "station_id": str(obj.station_id) if obj.station_id else None,
        "sku": obj.sku,
        "barcode": obj.barcode,
        "name_ar": obj.name_ar,
        "name_en": obj.name_en,
        "tax_percent": _d(obj.tax_percent),
        "is_tax_exempt": obj.is_tax_exempt,
        "is_sellable": obj.is_sellable,
        "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def variant(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "product_id": str(obj.product_id),
        "name_ar": obj.name_ar,
        "sku": obj.sku,
        "price": _d(obj.price),
        "cost": _d(obj.cost),
        "is_default": obj.is_default,
        "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def modifier_group(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "name_ar": obj.name_ar,
        "min_select": obj.min_select,
        "max_select": obj.max_select,
        "is_required": obj.is_required,
        "sort_order": obj.sort_order,
    }


def modifier(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "group_id": str(obj.group_id),
        "name_ar": obj.name_ar,
        "price_delta": _d(obj.price_delta),
        "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def area(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "name_ar": obj.name_ar,
        "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def table(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "area_id": str(obj.area_id),
        "number": obj.number,
        "seats": obj.seats,
        "status": obj.status,
        "pos_x": obj.pos_x,
        "pos_y": obj.pos_y,
        # The furniture itself. A round two-top and a rectangular eight-top drawn
        # as identical squares is a map of a room nobody works in.
        "shape": obj.shape,
        "span_x": obj.span_x,
        "span_y": obj.span_y,
        "rotation": obj.rotation,
        "is_active": obj.is_active,
    }


def printer(obj) -> dict[str, Any]:
    """
    Everything a terminal needs to send a job, except where the cable is.

    `device_path` travels because a branch often standardises on one, but a
    terminal that has its own binding overrides it locally — a serial port is a
    property of a machine, not of a cafe.
    """
    return {
        "id": str(obj.id),
        "name_ar": obj.name_ar,
        "code": obj.code,
        "kind": obj.kind,
        "connection": obj.connection,
        "host": obj.host,
        "port": obj.port,
        "device_path": obj.device_path,
        "paper_width_mm": obj.paper_width_mm,
        "dots": obj.dots,
        "copies": obj.copies,
        "cut_after": obj.cut_after,
        "is_default": obj.is_default,
        "station_ids": [str(pk) for pk in obj.stations.values_list("id", flat=True)],
        "is_active": obj.is_active,
    }


def station(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "code": obj.code,
        "name_ar": obj.name_ar,
        "target_prep_minutes": obj.target_prep_minutes,
        "auto_accept": obj.auto_accept,
        "printer_name": obj.printer_name,
        "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def payment_method(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "code": obj.code,
        "name_ar": obj.name_ar,
        "counts_as_cash": obj.counts_as_cash,
        "requires_reference": obj.requires_reference,
        "is_active": obj.is_active,
    }


def user(obj) -> dict[str, Any]:
    """
    Note what is NOT here: no password hash, and no session material.

    The PIN hash IS mirrored, because verifying a manager's step-up PIN during
    an outage is the whole point of caching staff at all — but it is a hash, and
    the device never sees anything that would let it mint a session.
    """
    return {
        "id": str(obj.id),
        "email": obj.email,
        "full_name_ar": obj.full_name_ar,
        "pin_hash": obj.pin_hash,
        "is_active": obj.is_active,
    }


def role_assignment(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "user_id": str(obj.user_id),
        "role_id": str(obj.role_id),
        "role_code": obj.role.code,
        "branch_id": str(obj.branch_id) if obj.branch_id else None,
        "permissions": sorted(obj.role.permission_codes),
    }


def setting_value(obj) -> dict[str, Any]:
    return {
        "id": str(obj.scope_id),
        "scope_type": obj.scope_type,
        "scope_id": str(obj.scope_id),
        "key": obj.key,
        "value": obj.value,
    }


def play_area(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "name_ar": obj.name_ar,
        "max_capacity": obj.max_capacity,
        "min_age_months": obj.min_age_months,
        "max_age_months": obj.max_age_months,
        "requires_socks": obj.requires_socks,
        "billing_variant_id": str(obj.billing_variant_id) if obj.billing_variant_id else None,
        "is_active": obj.is_active,
    }


def play_tariff(obj) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "area_id": str(obj.area_id),
        "name_ar": obj.name_ar,
        "mode": obj.mode,
        "entry_fee": _d(obj.entry_fee),
        "included_minutes": obj.included_minutes,
        "package_minutes": obj.package_minutes,
        "block_minutes": obj.block_minutes,
        "block_rate": _d(obj.block_rate),
        "grace_minutes": obj.grace_minutes,
        "daily_cap": _d(obj.daily_cap),
        "applies_days": obj.applies_days,
        "applies_from": obj.applies_from.strftime("%H:%M") if obj.applies_from else None,
        "applies_to": obj.applies_to.strftime("%H:%M") if obj.applies_to else None,
        "priority": obj.priority,
        "is_default": obj.is_default,
        "is_active": obj.is_active,
    }


def order_event(obj) -> dict[str, Any]:
    """
    What lets a floor tablet and a cashier terminal see the same table.

    The EVENT is shipped, not the folded order, so the receiving device runs the
    identical fold and cannot end up with a different total than the server's.
    """
    return {
        "id": str(obj.id),
        "order_id": str(obj.order_id),
        "sequence": obj.sequence,
        "event_type": obj.event_type,
        "payload": obj.payload,
        "device_id": str(obj.device_id) if obj.device_id else None,
        "actor_id": str(obj.actor_id) if obj.actor_id else None,
        "occurred_at": _t(obj.occurred_at),
        "recorded_at": _t(obj.recorded_at),
    }
