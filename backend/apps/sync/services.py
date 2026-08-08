"""
The push and pull engine.

`apply_push` is the piece worth reading carefully. Three properties it has to
hold at once, and each of them is a way real POS systems lose money:

  1. **A replay changes nothing.** `op_uuid` is UNIQUE, checked before any work
     happens, so a batch resent after a timeout returns the original results.
  2. **One bad operation does not poison the batch.** Each operation runs in its
     own savepoint. An all-or-nothing batch means a single malformed row blocks
     a terminal indefinitely — it retries forever, and the forty-nine good sales
     behind it never arrive.
  3. **Nothing fails silently.** Every outcome is recorded, and anything a human
     needs to see becomes a `SyncConflict` rather than a log line nobody reads.

A sync engine that fails quietly is worse than no sync engine: staff keep
working, confident everything is recorded, and find out a week later that a
terminal has been queueing since Tuesday.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError, ConflictError

from . import changelog
from .handlers import HANDLERS
from .models import (
    DeviceCursor,
    OperationStatus,
    Stream,
    SyncConflict,
    SyncOperation,
)

logger = logging.getLogger(__name__)


@dataclass
class PushResult:
    results: list[dict] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return sum(1 for r in self.results if r["status"] == OperationStatus.APPLIED)

    @property
    def failed(self) -> int:
        return sum(
            1
            for r in self.results
            if r["status"] in (OperationStatus.CONFLICT, OperationStatus.REJECTED)
        )

    @property
    def is_mixed(self) -> bool:
        """207 Multi-Status when some operations landed and others did not."""
        return self.applied > 0 and self.failed > 0


def apply_push(
    *,
    device,
    branch,
    operations: list[dict],
    batch_id=None,
    actor=None,
) -> PushResult:
    """
    Apply a batch of pushed operations.

    NOT wrapped in a single outer transaction. That is deliberate and is the
    whole point of the savepoint-per-operation design: an outer transaction that
    rolled back on the last operation would discard the forty-nine before it,
    and the device — having been told nothing succeeded — would resend all fifty
    forever.
    """
    result = PushResult()
    now = timezone.now()
    skew_limit = _skew_limit(branch)

    for raw in operations:
        op_uuid = str(raw["op_uuid"])
        entity_type = raw.get("entity_type", "")

        record, created = SyncOperation.objects.get_or_create(
            op_uuid=op_uuid,
            defaults={
                "device": device,
                "branch": branch,
                "actor": actor,
                "batch_id": batch_id,
                "entity_type": entity_type,
                "entity_id": raw.get("entity_id"),
                "payload": raw.get("payload", {}),
                "client_seq": raw.get("client_seq"),
                "aggregate_seq": raw.get("aggregate_seq"),
                "client_time": raw.get("client_time"),
                "received_at": now,
            },
        )

        if not created:
            # A replay. Return what happened the first time — identically, so a
            # client cannot tell a retry from the original and act differently.
            result.results.append(
                {
                    "op_uuid": op_uuid,
                    "status": record.status,
                    "result": record.result,
                    "code": record.error_code or None,
                    "replayed": True,
                }
            )
            continue

        _record_clock_skew(record, now=now, limit=skew_limit)

        handler = HANDLERS.get(entity_type)
        if handler is None:
            _reject(record, code="UNKNOWN_ENTITY_TYPE", message=f"نوع غير معروف: {entity_type}")
            result.results.append(
                {
                    "op_uuid": op_uuid,
                    "status": OperationStatus.REJECTED,
                    "code": "UNKNOWN_ENTITY_TYPE",
                }
            )
            continue

        try:
            with transaction.atomic():  # savepoint — one bad op stays one bad op
                payload = handler(
                    device=device,
                    branch=branch,
                    payload=raw.get("payload", {}),
                    actor=actor,
                )
        except ConflictError as exc:
            _conflict(record, exc)
            result.results.append(
                {
                    "op_uuid": op_uuid,
                    "status": OperationStatus.CONFLICT,
                    "code": record.error_code,
                    "server_state": getattr(exc, "extra", {}) or {},
                }
            )
        except AppError as exc:
            # 422-class: structurally invalid, and it will be invalid forever.
            # Never retried — that is the difference between a queue that drains
            # and a queue that grinds.
            _reject(record, code=getattr(exc, "code", "REJECTED"), message=str(exc.detail))
            result.results.append(
                {"op_uuid": op_uuid, "status": OperationStatus.REJECTED, "code": record.error_code}
            )
        except Exception as exc:
            logger.exception(
                "Sync handler crashed", extra={"op_uuid": op_uuid, "entity": entity_type}
            )
            _reject(record, code="HANDLER_ERROR", message=str(exc)[:500])
            result.results.append(
                {"op_uuid": op_uuid, "status": OperationStatus.REJECTED, "code": "HANDLER_ERROR"}
            )
        else:
            record.status = OperationStatus.APPLIED
            record.result = payload or {}
            record.applied_at = timezone.now()
            record.save(update_fields=["status", "result", "applied_at", "updated_at"])
            result.results.append(
                {"op_uuid": op_uuid, "status": OperationStatus.APPLIED, "result": record.result}
            )

    if device is not None:
        device.last_seen_at = timezone.now()
        device.save(update_fields=["last_seen_at"])

    return result


def _skew_limit(branch) -> int:
    context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
    return resolver.get("sync.max_clock_skew_seconds", context)


def _record_clock_skew(record: SyncOperation, *, now, limit: int) -> None:
    """
    Record the device's clock error. Never act on it.

    The server's clock is authoritative for everything that matters — an event's
    `recorded_at`, a shift's close time — so a skewed terminal produces correct
    records with a wrong-looking `occurred_at`. What a skewed clock DOES break is
    a human reading the audit trail, so it is surfaced loudly rather than
    silently accepted, and never used to reject a sale that really happened.
    """
    if record.client_time is None:
        return

    skew = int((record.client_time - now).total_seconds())
    record.clock_skew_seconds = skew
    record.save(update_fields=["clock_skew_seconds", "updated_at"])

    if abs(skew) > limit:
        logger.warning(
            "Device clock skew beyond tolerance",
            extra={
                "device": str(record.device_id),
                "skew_seconds": skew,
                "limit_seconds": limit,
                "op_uuid": str(record.op_uuid),
            },
        )
        SyncConflict.objects.create(
            operation=record,
            branch=record.branch,
            code="CLOCK_SKEW",
            message_ar=f"ساعة الجهاز تختلف عن الخادم بمقدار {abs(skew)} ثانية",
            server_state={
                "skew_seconds": skew,
                "limit_seconds": limit,
                "server_time": now.isoformat(),
                "client_time": record.client_time.isoformat(),
            },
        )


def _conflict(record: SyncOperation, exc: ConflictError) -> None:
    record.status = OperationStatus.CONFLICT
    record.error_code = getattr(exc, "code", "CONFLICT")
    record.error_message = str(exc.detail)[:500]
    record.save(update_fields=["status", "error_code", "error_message", "updated_at"])

    SyncConflict.objects.create(
        operation=record,
        branch=record.branch,
        code=record.error_code,
        message_ar=str(exc.detail)[:250],
        server_state=getattr(exc, "extra", {}) or {},
    )


def _reject(record: SyncOperation, *, code: str, message: str) -> None:
    record.status = OperationStatus.REJECTED
    record.error_code = code[:48]
    record.error_message = message[:500]
    record.save(update_fields=["status", "error_code", "error_message", "updated_at"])

    logger.warning(
        "Sync operation rejected",
        extra={"op_uuid": str(record.op_uuid), "code": code, "entity": record.entity_type},
    )


# ── pull ─────────────────────────────────────────────────────────────────────


def pull(*, branch, stream: str, cursor: int, limit: int = 500, device=None) -> dict:
    """
    Hand a device everything it has not seen on one stream.

    The returned cursor is the seq of the last row IN THIS RESPONSE, never the
    current head. A device that fails to process a batch simply re-asks from the
    same place; a cursor that ran ahead of what was actually delivered would skip
    those rows forever.
    """
    if stream not in Stream.values:
        raise AppError(f"مسار غير معروف: {stream}", code="UNKNOWN_STREAM")

    rows = list(
        changelog.visible_changes(branch_id=branch.id, stream=stream, after=cursor)[: limit + 1]
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = rows[-1].seq if rows else cursor

    if device is not None:
        DeviceCursor.objects.update_or_create(
            device=device,
            stream=stream,
            defaults={"cursor": next_cursor, "last_pulled_at": timezone.now()},
        )

    return {
        "stream": stream,
        "cursor": next_cursor,
        "has_more": has_more,
        "changes": [
            {
                "seq": row.seq,
                "entity_type": row.entity_type,
                "entity_id": str(row.entity_id),
                "operation": row.operation,
                "payload": row.payload,
            }
            for row in rows
        ],
    }


# ── status ───────────────────────────────────────────────────────────────────


def device_status(device) -> dict:
    """
    What the Web Admin shows per terminal, and what a notification fires on.

    `pending` counts operations the server accepted but has not applied. It is
    normally zero; anything else means something is stuck and a human should
    know before a customer does.
    """
    operations = SyncOperation.objects.filter(device=device)
    conflicts = SyncConflict.objects.filter(
        operation__device=device, resolved_at__isnull=True
    ).count()

    last = operations.order_by("-received_at").values_list("received_at", flat=True).first()

    return {
        "device_id": str(device.id),
        "device_name": device.device_name,
        "status": device.status,
        "app_version": device.app_version,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "last_push_at": last.isoformat() if last else None,
        "pending": operations.filter(status=OperationStatus.PENDING).count(),
        "rejected": operations.filter(status=OperationStatus.REJECTED).count(),
        "open_conflicts": conflicts,
        "cursors": {row.stream: row.cursor for row in DeviceCursor.objects.filter(device=device)},
    }


def branch_status(branch) -> dict:
    """
    The whole branch at a glance — what the owner watches over the internet.

    Devices unseen for longer than `sync.offline_alert_minutes` during business
    hours are the alert that matters: a terminal that stopped talking is a
    terminal whose sales are sitting on a hard drive.
    """
    from apps.licensing.models import Device

    context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
    threshold = resolver.get("sync.offline_alert_minutes", context)
    cutoff = timezone.now() - timedelta(minutes=threshold)

    rows = list(Device.objects.filter(branch=branch))
    stale = sum(1 for d in rows if d.last_seen_at is None or d.last_seen_at < cutoff)

    return {
        "branch_id": str(branch.id),
        "devices": [device_status(d) for d in rows],
        "stale_devices": stale,
        "offline_alert_minutes": threshold,
        "open_conflicts": SyncConflict.objects.filter(
            branch=branch, resolved_at__isnull=True
        ).count(),
        "heads": {s: changelog.head(branch_id=branch.id, stream=s) for s in Stream.values},
    }


# ── conflict resolution ──────────────────────────────────────────────────────


RESOLUTIONS = ("ACKNOWLEDGED", "RETRIED", "DISCARDED")


@transaction.atomic
def resolve_conflict(conflict: SyncConflict, *, resolution: str, note: str = "", user=None):
    """
    Close a conflict.

    `RETRIED` re-runs the original operation — the useful case is
    `ORDER_ALREADY_CLOSED`, where the remedy is usually to move the items onto a
    new order and then acknowledge. `DISCARDED` records a deliberate decision to
    drop the operation, with who decided it, because "we chose not to record
    that sale" must be a signed statement rather than a gap.
    """
    if resolution not in RESOLUTIONS:
        raise AppError(f"قرار غير معروف: {resolution}", code="UNKNOWN_RESOLUTION")
    if not conflict.is_open:
        raise AppError("التعارض محلول بالفعل", code="ALREADY_RESOLVED")

    operation = conflict.operation

    if resolution == "RETRIED":
        handler = HANDLERS.get(operation.entity_type)
        if handler is None:
            raise AppError("لا يمكن إعادة المحاولة لهذا النوع", code="NOT_RETRYABLE")
        result = handler(
            device=operation.device,
            branch=operation.branch,
            payload=operation.payload,
            actor=user,
        )
        operation.status = OperationStatus.APPLIED
        operation.result = result or {}
        operation.applied_at = timezone.now()
        operation.error_code = ""
        operation.save(update_fields=["status", "result", "applied_at", "error_code", "updated_at"])

    conflict.resolved_at = timezone.now()
    conflict.resolved_by = user
    conflict.resolution = resolution
    conflict.resolution_note = note[:250]
    conflict.save(
        update_fields=["resolved_at", "resolved_by", "resolution", "resolution_note", "updated_at"]
    )

    from apps.audit import services as audit

    audit.record(
        "sync.conflict_resolved",
        branch=conflict.branch,
        actor=user,
        obj=conflict,
        object_label=conflict.code,
        detail={
            "resolution": resolution,
            "note": note,
            "entity_type": operation.entity_type,
            "op_uuid": str(operation.op_uuid),
        },
    )
    logger.info(
        "Sync conflict resolved",
        extra={"conflict": str(conflict.id), "code": conflict.code, "resolution": resolution},
    )
    return conflict
