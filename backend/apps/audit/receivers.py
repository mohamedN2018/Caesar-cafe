"""
Model-level audit receivers.

Only for the actions that ARE a row edit and carry their own reason: a price
change already writes a `PriceHistory` row, a setting change already writes a
`SettingChangeLog` row, a licence action already writes a `LicenseEvent`. Those
tables are the source; this mirrors them into the one place a manager looks.

Everything else — a void, a refund, a posted count — is recorded by the service
that performs it, because only the service knows the reason, the approver, and
whether the thing succeeded. A `post_save` receiver on `Order` cannot tell a void
from a status change, and guessing is how an audit trail starts lying.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from . import services

logger = logging.getLogger(__name__)


@receiver(post_save, sender="catalog.PriceHistory", weak=False, dispatch_uid="audit:price")
def _on_price_change(sender, instance, created, **kwargs):
    """
    A price change is the single most consequential edit in the catalog: it
    changes what every future customer is charged, and the person who made it is
    rarely the person who notices.
    """
    if not created:
        return

    variant = instance.variant
    services.record(
        "catalog.price_changed",
        branch=variant.product.branch,
        actor=instance.changed_by,
        obj=variant,
        object_label=str(variant),
        before={"price": instance.old_price},
        after={"price": instance.new_price},
        detail={"reason": instance.reason, "sku": variant.sku},
    )


@receiver(
    post_save, sender="configuration.SettingChangeLog", weak=False, dispatch_uid="audit:setting"
)
def _on_setting_change(sender, instance, created, **kwargs):
    """
    "Who changed the service charge and when" is the first question asked when a
    total looks wrong.
    """
    if not created:
        return

    from apps.organizations.models import Branch, Organization

    branch = None
    organization = None
    if instance.scope_type == "BRANCH":
        branch = Branch.objects.filter(id=instance.scope_id).first()
    elif instance.scope_type == "ORGANIZATION":
        organization = Organization.objects.filter(id=instance.scope_id).first()

    services.record(
        "system.setting_changed",
        branch=branch,
        organization=organization,
        actor=instance.changed_by,
        object_type="setting",
        object_id=instance.key,
        object_label=instance.key,
        before={"value": instance.old_value},
        after={"value": instance.new_value},
        detail={"scope": instance.scope_type, "key": instance.key},
    )


@receiver(post_save, sender="licensing.LicenseEvent", weak=False, dispatch_uid="audit:license")
def _on_license_event(sender, instance, created, **kwargs):
    """
    Mirror the licence trail. Only the events that change what a customer is
    entitled to — a heartbeat every five minutes would bury everything else.
    """
    if not created:
        return

    mapping = {
        "CREATED": "license.created",
        "ACTIVATED": "license.activated",
        "SUSPENDED": "license.suspended",
        "RESUMED": "license.renewed",
        "REVOKED": "license.revoked",
        "RENEWED": "license.renewed",
        "SEATS_CHANGED": "license.renewed",
        "KEY_REGENERATED": "license.renewed",
        "DEVICE_RESET": "device.reset",
        "DEVICE_REVOKED": "device.reset",
    }
    action = mapping.get(instance.event)
    if action is None:
        return

    services.record(
        action,
        organization=instance.license.organization,
        branch=instance.license.branch,
        actor=instance.actor,
        obj=instance.license,
        object_label=instance.license.key_prefix,
        detail={
            "event": instance.event,
            "device": str(instance.device_id) if instance.device_id else None,
            **(instance.detail or {}),
        },
    )


def _became_inactive(sender, instance) -> bool:
    """
    True when this save flips `is_active` from True to False.

    Needs the previous row, so it runs on `pre_save`. A `post_save` receiver
    cannot tell a deactivation from a save that merely happened to have
    `is_active=False` already, and recording the second one would produce a
    stream of false deactivations every time anything else on the row changed.
    """
    if instance.pk is None or instance.is_active:
        return False
    previous = sender.objects.filter(pk=instance.pk).values_list("is_active", flat=True).first()
    return previous is True


@receiver(pre_save, sender="catalog.Product", weak=False, dispatch_uid="audit:product_off")
def _on_product_deactivated(sender, instance, **kwargs):
    """
    A product is deactivated, never deleted — deleting it would orphan every
    historical line item. This records who took it off the menu.
    """
    if not _became_inactive(sender, instance):
        return

    services.record(
        "catalog.product_deactivated",
        branch=instance.branch,
        obj=instance,
        object_label=f"{instance.name_ar} ({instance.sku})",
        before={"is_active": True},
        after={"is_active": False},
    )


@receiver(post_save, sender="recipes.RecipeLine", weak=False, dispatch_uid="audit:recipe")
def _on_recipe_line(sender, instance, **kwargs):
    """
    A recipe change silently re-prices the cost of everything that uses it, and
    therefore every margin report. It is not a financial record itself, which is
    exactly why it is easy to change without anyone connecting the two.
    """
    recipe = instance.recipe
    variant = getattr(recipe, "variant", None)

    services.record(
        "catalog.recipe_changed",
        branch=variant.product.branch if variant else None,
        obj=recipe,
        object_label=str(variant) if variant else str(recipe),
        after={"item": instance.item.name_ar, "quantity": instance.quantity},
        detail={"item": instance.item.name_ar, "quantity": instance.quantity},
    )


@receiver(post_save, sender="accounts.User", weak=False, dispatch_uid="audit:user_created")
def _on_user_created(sender, instance, created, **kwargs):
    if not created:
        return

    services.record(
        "staff.user_created",
        organization=instance.organization,
        obj=instance,
        object_label=instance.full_name_ar or instance.email,
        after={"email": instance.email, "full_name_ar": instance.full_name_ar},
    )


@receiver(pre_save, sender="accounts.User", weak=False, dispatch_uid="audit:user_off")
def _on_user_deactivated(sender, instance, **kwargs):
    """A deactivated account is the security-relevant half of staff management."""
    if not _became_inactive(sender, instance):
        return

    services.record(
        "staff.user_deactivated",
        organization=instance.organization,
        obj=instance,
        object_label=instance.full_name_ar or instance.email,
        before={"is_active": True},
        after={"is_active": False},
    )


@receiver(post_save, sender="authz.RoleAssignment", weak=False, dispatch_uid="audit:role")
def _on_role_assignment(sender, instance, created, **kwargs):
    """
    E1: a cashier granting themselves permissions. The grant itself is gated by
    step-up; this makes it findable afterwards.
    """
    if not created:
        return

    services.record(
        "staff.role_changed",
        organization=instance.user.organization,
        branch=instance.branch,
        obj=instance.user,
        object_label=instance.user.full_name_ar or instance.user.email,
        after={"role": instance.role.code, "branch": str(instance.branch_id or "ALL")},
        detail={"role": instance.role.code, "scope": str(instance.branch_id or "ALL")},
    )
