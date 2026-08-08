"""
Pulling the mirror.

Cursor-based, never timestamp-based — the server hands out a monotonic `seq` and
this client stores exactly what it was given. The three ways a timestamp window
breaks (clock skew, transaction visibility, resolution ties) are the server's
problem to have solved; the client's job is simply not to invent its own
watermark.

Two rules here:

  * **The cursor advances only after the batch is applied**, in the same
    transaction. A crash between applying rows and saving the cursor would
    re-apply them next time — harmless, because every write is an upsert — but a
    cursor saved before the rows would skip them forever.

  * **An unknown entity type is skipped, not fatal.** A server newer than this
    client will send things it has never heard of, and a terminal that refused to
    sync because of a field it does not use is a terminal that stops selling over
    a feature it does not have.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from ..api.client import ApiClient, ApiError, NetworkUnavailable
from ..local.db import Database, transaction

logger = logging.getLogger(__name__)

#: Stream → poll interval in seconds. Staleness tolerances genuinely differ: a
#: price list may be a minute old; another terminal's open table may not.
STREAMS: dict[str, int] = {
    "config": 300,
    "catalog": 60,
    "floor": 60,
    "staff": 300,
    "kids": 300,
    "orders": 10,
}

#: entity_type → (table, the columns lifted out of the payload for querying).
#: Everything else stays inside `payload`, so a field this client does not know
#: about survives to the next release rather than being silently dropped.
ENTITY_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "category": ("m_categories", ("parent_id", "name_ar", "sort_order", "is_active")),
    "product": (
        "m_products",
        ("category_id", "station_id", "sku", "name_ar", "is_sellable", "sort_order", "is_active"),
    ),
    "variant": (
        "m_variants",
        ("product_id", "name_ar", "sku", "price", "cost", "is_default", "is_active"),
    ),
    "modifier_group": ("m_modifier_groups", ("name_ar",)),
    "modifier": ("m_modifiers", ("group_id", "name_ar", "price_delta", "is_active")),
    "area": ("m_areas", ("name_ar",)),
    "table": ("m_tables", ("area_id", "number", "seats", "status")),
    "station": ("m_stations", ("code", "name_ar")),
    "payment_method": ("m_payment_methods", ("code", "name_ar", "counts_as_cash", "is_active")),
    "user": ("m_users", ("email", "full_name_ar", "pin_hash", "is_active")),
    "play_area": ("m_kids_areas", ("name_ar",)),
    "play_tariff": ("m_kids_tariffs", ("area_id", "name_ar")),
}


@dataclass(frozen=True)
class PullOutcome:
    stream: str
    applied: int = 0
    cursor: int = 0
    has_more: bool = False
    offline: bool = False
    error: str = ""


def pull_stream(db: Database, client: ApiClient, stream: str, *, limit: int = 500) -> PullOutcome:
    cursor = db.scalar("SELECT cursor FROM m_sync_meta WHERE stream = ?", (stream,), default=0)

    try:
        response = client.get(
            "/sync/pull/", params={"stream": stream, "cursor": cursor, "limit": limit}
        )
    except NetworkUnavailable as exc:
        return PullOutcome(stream=stream, cursor=cursor, offline=True, error=str(exc))
    except ApiError as exc:
        _record_error(db, stream, exc.code)
        return PullOutcome(stream=stream, cursor=cursor, error=exc.code)

    changes = response.get("changes", [])
    new_cursor = response.get("cursor", cursor)

    applied = 0
    with transaction(db.connection):
        for change in changes:
            if _apply_change(db, change):
                applied += 1

        # Same transaction as the rows. A cursor saved before them would skip
        # those rows forever.
        db.execute(
            """
            INSERT INTO m_sync_meta (stream, cursor, last_pull_at, last_error)
            VALUES (?, ?, ?, '')
            ON CONFLICT(stream) DO UPDATE
            SET cursor = excluded.cursor, last_pull_at = excluded.last_pull_at, last_error = ''
            """,
            (stream, new_cursor, datetime.now(UTC).isoformat()),
        )

    return PullOutcome(
        stream=stream,
        applied=applied,
        cursor=new_cursor,
        has_more=bool(response.get("has_more")),
    )


def _apply_change(db: Database, change: dict) -> bool:
    entity_type = change.get("entity_type", "")
    entity_id = change.get("entity_id")
    operation = change.get("operation", "UPSERT")
    payload = change.get("payload") or {}

    if entity_type == "setting":
        return _apply_setting(db, payload, operation)
    if entity_type == "role_assignment":
        return _apply_permissions(db, entity_id, payload, operation)
    if entity_type == "order_event":
        return _apply_order_event(db, payload)

    mapping = ENTITY_TABLES.get(entity_type)
    if mapping is None:
        # A server newer than this client. Skipping is correct: a terminal that
        # refused to sync over a feature it does not have would stop selling.
        logger.debug("Ignoring unknown entity type", extra={"entity_type": entity_type})
        return False

    table, columns = mapping
    if operation == "DELETE":
        db.delete_mirror(table, entity_id)
        return True

    row = {"id": entity_id, "payload": json.dumps(payload, ensure_ascii=False)}
    for column in columns:
        # Only lift a column the payload actually carries. Writing None for an
        # absent key would override the schema default on insert and blank a
        # good value on update — a server that stops sending a field it no
        # longer uses must not empty this terminal's copy of it.
        if column in payload:
            row[column] = _coerce(payload[column])

    db.upsert_mirror(table, row)
    return True


def _apply_setting(db: Database, payload: dict, operation: str) -> bool:
    key = payload.get("key")
    if not key:
        return False

    if operation == "DELETE":
        db.delete_mirror("m_settings", key, key="key")
        return True

    db.upsert_mirror(
        "m_settings",
        {
            "key": key,
            "value": json.dumps(payload.get("value"), ensure_ascii=False),
            "payload": json.dumps(payload, ensure_ascii=False),
        },
        key="key",
    )
    return True


def _apply_permissions(db: Database, entity_id, payload: dict, operation: str) -> bool:
    """
    A revoked assignment must not survive in the cache.

    This is the one mirror update that is a security control rather than a
    convenience: a manager removed from the system must lose their step-up
    authority here too, and a DELETE that was ignored would leave it intact.
    """
    if operation == "DELETE":
        db.delete_mirror("m_permissions", str(entity_id))
        return True

    db.upsert_mirror(
        "m_permissions",
        {
            "id": str(entity_id),
            "user_id": payload.get("user_id", ""),
            "role_code": payload.get("role_code", ""),
            "permissions": json.dumps(payload.get("permissions", []), ensure_ascii=False),
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    return True


def _apply_order_event(db: Database, payload: dict) -> bool:
    """
    An event from ANOTHER device on this branch.

    Stored, not folded here: the fold is `orders.apply` and runs the same
    arithmetic the server does. Events this device produced come back through
    the stream too and are ignored by the UNIQUE constraint — which is exactly
    the idempotency the event id exists for.
    """
    try:
        db.insert(
            "l_order_events",
            {
                "id": payload["id"],
                "order_id": payload["order_id"],
                "sequence": payload.get("sequence", 0),
                "event_type": payload.get("event_type", ""),
                "payload": json.dumps(payload.get("payload") or {}, ensure_ascii=False),
                "occurred_at": payload.get("occurred_at") or datetime.now(UTC).isoformat(),
                "actor_id": payload.get("actor_id"),
            },
        )
        return True
    except Exception:  # sqlite3.IntegrityError — already have it
        return False


def _record_error(db: Database, stream: str, code: str) -> None:
    with transaction(db.connection):
        db.execute(
            """
            INSERT INTO m_sync_meta (stream, cursor, last_error)
            VALUES (?, 0, ?)
            ON CONFLICT(stream) DO UPDATE SET last_error = excluded.last_error
            """,
            (stream, code),
        )


def _coerce(value):
    """JSON true/false become SQLite 1/0; dicts and lists become JSON text."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def cursors(db: Database) -> dict[str, int]:
    return {row["stream"]: row["cursor"] for row in db.query("SELECT * FROM m_sync_meta")}
