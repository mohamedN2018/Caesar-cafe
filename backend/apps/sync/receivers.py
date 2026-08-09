"""
Keeping the mirror streams fed.

Signals rather than explicit calls in every viewset, deliberately. A price
changed through the admin, through a management command, through a data
migration or through a future endpoint nobody has written yet must all reach the
terminals. An explicit `record_change()` at every write site is one refactor away
from a Desktop running silently on last month's prices, and that failure is
invisible until a customer is charged the wrong amount.

The cost is the usual one: signals are action at a distance. It is paid down by
keeping every receiver here, in one file, next to the table that says which
model feeds which stream.

Deletion is a soft delete everywhere in this system, so `post_delete` is
registered only as a backstop for the genuinely-removed rows (a role assignment
being revoked, a setting override being cleared). A revoked assignment that
lingered in a local cache would be a real security problem — it is the one case
where "eventually consistent" is not good enough, which is why `staff` pulls
every 5 minutes and the device re-checks on every heartbeat.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from . import payloads
from .changelog import record
from .models import Operation, Stream


def _emit(instance, *, stream: str, entity_type: str, payload_fn, operation: str, branch_id=None):
    record(
        branch_id=branch_id if branch_id is not None else getattr(instance, "branch_id", None),
        stream=stream,
        entity_type=entity_type,
        entity_id=instance.id,
        payload=payload_fn(instance) if operation == Operation.UPSERT else {},
        operation=operation,
    )


def _register(model_path: str, *, stream: str, entity_type: str, payload_fn, branch_of=None):
    """
    Wire one model into one stream.

    `branch_of` names a callable that finds the branch for models that do not
    carry one directly — a variant belongs to a product, a table to an area.
    """

    def _branch_id(instance):
        return branch_of(instance) if branch_of else getattr(instance, "branch_id", None)

    @receiver(post_save, sender=model_path, weak=False, dispatch_uid=f"sync:save:{entity_type}")
    def _on_save(sender, instance, **kwargs):
        _emit(
            instance,
            stream=stream,
            entity_type=entity_type,
            payload_fn=payload_fn,
            operation=Operation.UPSERT,
            branch_id=_branch_id(instance),
        )

    @receiver(post_delete, sender=model_path, weak=False, dispatch_uid=f"sync:del:{entity_type}")
    def _on_delete(sender, instance, **kwargs):
        _emit(
            instance,
            stream=stream,
            entity_type=entity_type,
            payload_fn=payload_fn,
            operation=Operation.DELETE,
            branch_id=_branch_id(instance),
        )

    return _on_save, _on_delete


# ── catalog ──────────────────────────────────────────────────────────────────

_register(
    "catalog.Category", stream=Stream.CATALOG, entity_type="category", payload_fn=payloads.category
)
_register(
    "catalog.Product", stream=Stream.CATALOG, entity_type="product", payload_fn=payloads.product
)
_register(
    "catalog.ProductVariant",
    stream=Stream.CATALOG,
    entity_type="variant",
    payload_fn=payloads.variant,
    branch_of=lambda v: v.product.branch_id,
)
_register(
    "catalog.ModifierGroup",
    stream=Stream.CATALOG,
    entity_type="modifier_group",
    payload_fn=payloads.modifier_group,
)
_register(
    "catalog.Modifier",
    stream=Stream.CATALOG,
    entity_type="modifier",
    payload_fn=payloads.modifier,
    branch_of=lambda m: m.group.branch_id,
)

# ── floor ────────────────────────────────────────────────────────────────────

_register("floor.Area", stream=Stream.FLOOR, entity_type="area", payload_fn=payloads.area)
_register(
    "floor.Table",
    stream=Stream.FLOOR,
    entity_type="table",
    payload_fn=payloads.table,
    branch_of=lambda t: t.area.branch_id,
)
_register(
    "kitchen.Station", stream=Stream.FLOOR, entity_type="station", payload_fn=payloads.station
)
# Printers ride the CONFIG stream rather than FLOOR: they change rarely and a
# terminal that is a minute behind on which printer is the default has lost
# nothing, whereas an open table one minute stale is a double-seated party.
_register(
    "printing.Printer", stream=Stream.CONFIG, entity_type="printer", payload_fn=payloads.printer
)


@receiver(
    m2m_changed,
    # The through table only, or this fires for every many-to-many in the system.
    sender="printing.Printer_stations",
    weak=False,
    dispatch_uid="sync:m2m:printer_stations",
)
def _on_printer_stations_changed(sender, instance, action, reverse, **kwargs):
    """
    Re-emit a printer when its stations change.

    `post_save` fires BEFORE a serializer writes a many-to-many, so the payload
    that receiver produced carries an empty `station_ids`. Without this, a
    kitchen printer assigned to the grill would sync as belonging to no station
    at all — and the terminal, finding no match, would route every grill ticket
    to the branch default instead. The one fact this feature exists to carry
    would be the one fact that never arrives.
    """
    from apps.printing.models import Printer

    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    printers = [instance] if not reverse else Printer.objects.filter(stations=instance)
    for printer in printers:
        _emit(
            printer,
            stream=Stream.CONFIG,
            entity_type="printer",
            payload_fn=payloads.printer,
            operation=Operation.UPSERT,
            branch_id=printer.branch_id,
        )


# ── config ───────────────────────────────────────────────────────────────────

_register(
    "payments.PaymentMethod",
    stream=Stream.CONFIG,
    entity_type="payment_method",
    payload_fn=payloads.payment_method,
)

# ── kids ─────────────────────────────────────────────────────────────────────

_register(
    "kids.PlayArea", stream=Stream.KIDS, entity_type="play_area", payload_fn=payloads.play_area
)
_register(
    "kids.PlayTariff",
    stream=Stream.KIDS,
    entity_type="play_tariff",
    payload_fn=payloads.play_tariff,
    branch_of=lambda t: t.area.branch_id,
)

# ── staff ────────────────────────────────────────────────────────────────────


@receiver(post_save, sender="authz.RoleAssignment", weak=False, dispatch_uid="sync:save:assignment")
@receiver(
    post_delete, sender="authz.RoleAssignment", weak=False, dispatch_uid="sync:del:assignment"
)
def _on_role_assignment(sender, instance, **kwargs):
    """
    A permission change is the one mirror update that is a security control.

    Emitted to EVERY branch in the organization when the assignment is
    unscoped, because `branch = NULL` means "all branches" — sending it to none
    of them would leave a revoked manager working on every terminal.
    """
    from apps.organizations.models import Branch

    deleted = kwargs.get("signal") is post_delete
    operation = Operation.DELETE if deleted else Operation.UPSERT
    payload = {} if deleted else payloads.role_assignment(instance)

    branch_ids = (
        [instance.branch_id]
        if instance.branch_id
        else list(
            Branch.objects.filter(organization_id=instance.user.organization_id).values_list(
                "id", flat=True
            )
        )
    )
    for branch_id in branch_ids:
        record(
            branch_id=branch_id,
            stream=Stream.STAFF,
            entity_type="role_assignment",
            entity_id=instance.id,
            payload=payload,
            operation=operation,
        )


@receiver(post_save, sender="accounts.User", weak=False, dispatch_uid="sync:save:user")
def _on_user(sender, instance, **kwargs):
    from apps.organizations.models import Branch

    for branch_id in Branch.objects.filter(organization_id=instance.organization_id).values_list(
        "id", flat=True
    ):
        record(
            branch_id=branch_id,
            stream=Stream.STAFF,
            entity_type="user",
            entity_id=instance.id,
            payload=payloads.user(instance),
            operation=Operation.UPSERT,
        )


# ── settings ─────────────────────────────────────────────────────────────────


@receiver(
    post_save, sender="configuration.SettingValue", weak=False, dispatch_uid="sync:save:setting"
)
@receiver(
    post_delete, sender="configuration.SettingValue", weak=False, dispatch_uid="sync:del:setting"
)
def _on_setting(sender, instance, **kwargs):
    """
    Only BRANCH- and DEVICE-scoped overrides ship directly.

    An ORGANIZATION-scoped value affects every branch, so it fans out; a
    ROLE-scoped one is resolved server-side and reaches the device inside the
    permission payload instead.
    """
    from apps.organizations.models import Branch

    deleted = kwargs.get("signal") is post_delete
    operation = Operation.DELETE if deleted else Operation.UPSERT
    payload = {} if deleted else payloads.setting_value(instance)

    if instance.scope_type == "BRANCH":
        branch_ids = [instance.scope_id]
    elif instance.scope_type == "ORGANIZATION":
        branch_ids = list(
            Branch.objects.filter(organization_id=instance.scope_id).values_list("id", flat=True)
        )
    else:
        return

    for branch_id in branch_ids:
        record(
            branch_id=branch_id,
            stream=Stream.CONFIG,
            entity_type="setting",
            entity_id=instance.scope_id,
            payload={**payload, "key": instance.key},
            operation=operation,
        )


# ── orders ───────────────────────────────────────────────────────────────────


@receiver(post_save, sender="orders.OrderEvent", weak=False, dispatch_uid="sync:save:order_event")
def _on_order_event(sender, instance, created, **kwargs):
    """
    Ship the EVENT, never the folded order.

    A device that receives events runs the identical fold and therefore cannot
    reach a different total than the server. Shipping the projection instead
    would make the two agree only as long as nobody ever fixes a bug in the fold.
    """
    if not created:
        return
    record(
        branch_id=instance.order.branch_id,
        stream=Stream.ORDERS,
        entity_type="order_event",
        entity_id=instance.id,
        payload=payloads.order_event(instance),
        operation=Operation.UPSERT,
    )
