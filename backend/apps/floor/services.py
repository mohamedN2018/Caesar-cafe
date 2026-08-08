"""
Floor operations that move more than a status field.

Transferring a session is a table change. Merging two is a **money** change — it
moves orders between records, and afterwards there is one bill where there were
two. That is why it lives here with a transaction and an audit entry rather than
in a view.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import AppError, ConflictError

from .models import TableSession, TableStatus

logger = logging.getLogger(__name__)


@transaction.atomic
def merge_sessions(*, source: TableSession, target: TableSession, user=None) -> TableSession:
    """
    Fold `source` into `target`: one party, one bill, one table freed.

    The case this exists for is ordinary and currently unserveable — a group of
    eight arrives, two four-tops are pushed together, and at the end they want
    one bill. Without it a waiter either splits the party across two payments or
    re-rings every item onto one table, and re-ringing is how a round of drinks
    goes missing.

    Both sessions are locked before anything moves. Two waiters merging the same
    pair from two terminals would otherwise each read "one open session" and
    both close it, leaving orders pointing at a session that ended.

    What survives is `target`. Its guest count becomes the combined party,
    because that is now the number of people sitting there — and the floor view
    draws chairs from it.
    """
    locked_source = (
        TableSession.objects.select_for_update().select_related("table").get(pk=source.pk)
    )
    locked_target = (
        TableSession.objects.select_for_update().select_related("table").get(pk=target.pk)
    )

    if locked_source.pk == locked_target.pk:
        raise AppError("لا يمكن دمج الجلسة مع نفسها", code="SAME_SESSION")
    if not locked_source.is_open or not locked_target.is_open:
        raise ConflictError("إحدى الجلستين مغلقة بالفعل", code="SESSION_CLOSED")
    if locked_source.table.area_id != locked_target.table.area_id:
        # Tables in two different areas were not pushed together, so this is
        # almost certainly the wrong pair picked from a list. A transfer is the
        # operation for moving a party across the room.
        raise AppError(
            "الطاولتان في منطقتين مختلفتين — استخدم النقل بدلاً من الدمج",
            code="DIFFERENT_AREAS",
        )

    moved = list(locked_source.orders.all())
    locked_source.orders.update(table_session=locked_target)

    locked_target.guest_count += locked_source.guest_count
    locked_target.save(update_fields=["guest_count", "updated_at"])

    locked_source.closed_at = timezone.now()
    locked_source.save(update_fields=["closed_at", "updated_at"])

    # The freed table is AVAILABLE, not CLEANING: the party is still in the
    # room, the crockery went with them, and marking it dirty would send
    # somebody to wipe a table nobody left.
    freed = locked_source.table
    freed.status = TableStatus.AVAILABLE
    freed.save(update_fields=["status", "updated_at"])

    from apps.audit import services as audit

    audit.record(
        "floor.sessions_merged",
        branch=freed.area.branch,
        actor=user,
        obj=locked_target,
        object_label=f"{freed.number} → {locked_target.table.number}",
        detail={
            "from_table": freed.number,
            "to_table": locked_target.table.number,
            "orders_moved": len(moved),
            "guests": locked_target.guest_count,
        },
    )
    logger.info(
        "Table sessions merged",
        extra={
            "from_table": freed.number,
            "to_table": locked_target.table.number,
            "orders": len(moved),
        },
    )

    locked_target.refresh_from_db()
    return locked_target
