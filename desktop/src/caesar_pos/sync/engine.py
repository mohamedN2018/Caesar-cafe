"""
The sync coordinator and the honest status it reports.

The engine is a plain object with a `tick()`, driven by a Qt timer in the app and
called directly in tests. Keeping the schedule out of it means the whole engine
is testable without a running event loop — and a sync engine you can only test by
waiting is a sync engine nobody tests.

`SyncStatus` is the other half. docs/07:

    🟢 متصل            online, outbox empty
    🟡 يزامن (١٢)      online, 12 operations draining
    🔴 غير متصل (٤٥)   offline, 45 queued, last sync 14:02
    ⚠️ تعارض (٢)       2 operations need a human

The indicator is permanent and never optimistic. Staff must be able to glance at
it and know, which is the difference between an outage everyone works around and
one discovered a week later by an accountant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..api.client import ApiClient
from ..local import outbox
from ..local.db import Database
from . import puller, pusher

logger = logging.getLogger(__name__)


class State:
    ONLINE = "ONLINE"
    SYNCING = "SYNCING"
    OFFLINE = "OFFLINE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class SyncStatus:
    state: str
    pending: int = 0
    unresolved: int = 0
    last_success_at: datetime | None = None

    @property
    def icon(self) -> str:
        return {
            State.ONLINE: "🟢",
            State.SYNCING: "🟡",
            State.OFFLINE: "🔴",
            State.CONFLICT: "⚠️",
        }[self.state]

    @property
    def label_ar(self) -> str:
        if self.state == State.CONFLICT:
            return f"تعارض ({self.unresolved})"
        if self.state == State.OFFLINE:
            base = f"غير متصل ({self.pending})" if self.pending else "غير متصل"
            if self.last_success_at:
                return f"{base} — آخر مزامنة {self.last_success_at:%H:%M}"
            return base
        if self.state == State.SYNCING:
            return f"يزامن ({self.pending})"
        return "متصل"

    def __str__(self) -> str:
        return f"{self.icon} {self.label_ar}"


@dataclass
class SyncEngine:
    """
    Drains the outbox and pulls each stream at its own cadence.

    `tick()` is called on a short timer. It pushes on every tick — the outbox is
    the thing that costs money to delay — and pulls only the streams whose
    interval has elapsed.
    """

    db: Database
    client: ApiClient
    batch_size: int = pusher.DEFAULT_BATCH

    online: bool = True
    last_success_at: datetime | None = None
    _last_pull: dict[str, datetime] = field(default_factory=dict)

    # ── the loop ─────────────────────────────────────────────────────────────

    def tick(self, *, now: datetime | None = None) -> SyncStatus:
        now = now or datetime.now(UTC)

        push = pusher.drain_once(self.db, self.client, batch_size=self.batch_size, now=now)

        if push.offline:
            self.online = False
        elif push.attempted or self.online is False:
            # Any answered request proves the link is up, including one whose
            # operations were rejected — a rejection IS a reply.
            self.online = True

        if push.made_progress:
            self.last_success_at = now

        if self.online:
            for stream in self._due(now):
                outcome = puller.pull_stream(self.db, self.client, stream)
                self._record_pull(stream, outcome, now)

        return self.status()

    def _due(self, now: datetime) -> list[str]:
        due = []
        for stream, interval in puller.STREAMS.items():
            last = self._last_pull.get(stream)
            if last is None or (now - last).total_seconds() >= interval:
                due.append(stream)
        return due

    def _record_pull(self, stream: str, outcome, now: datetime) -> None:
        if outcome.offline:
            self.online = False
            return

        self._last_pull[stream] = now
        if outcome.applied:
            self.last_success_at = now
        # `has_more` means the next tick pulls again immediately rather than
        # waiting out the interval — a terminal returning from a long outage
        # should catch up in minutes, not over the next hour.
        if outcome.has_more:
            self._last_pull.pop(stream, None)

    def pull_now(self, stream: str) -> puller.PullOutcome:
        """Force one stream — used after activation, and by the refresh button."""
        outcome = puller.pull_stream(self.db, self.client, stream)
        self._record_pull(stream, outcome, datetime.now(UTC))
        return outcome

    def bootstrap(self) -> dict[str, int]:
        """
        Pull every stream to completion. Run once, after activation.

        A terminal with an empty mirror cannot sell — no products, no prices, no
        payment methods — so this is a blocking step with a progress dialog
        rather than something the background timer gets to eventually.
        """
        totals: dict[str, int] = {}

        for stream in puller.STREAMS:
            applied = 0
            while True:
                outcome = self.pull_now(stream)
                applied += outcome.applied
                if not outcome.has_more or outcome.offline:
                    break
            totals[stream] = applied

        logger.info("Bootstrap complete", extra={"streams": totals})
        return totals

    # ── status ───────────────────────────────────────────────────────────────

    def status(self) -> SyncStatus:
        counts = outbox.counts(self.db)

        if counts["unresolved"]:
            state = State.CONFLICT
        elif not self.online:
            state = State.OFFLINE
        elif counts["pending"]:
            state = State.SYNCING
        else:
            state = State.ONLINE

        return SyncStatus(
            state=state,
            pending=counts["pending"],
            unresolved=counts["unresolved"],
            last_success_at=self.last_success_at,
        )
