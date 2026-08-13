"""
Retry backoff (docs/07 §53).

`min(2^attempts, 300)` seconds with ±20% jitter — 2s, 4s, 8s … capped at five
minutes.

**The jitter is the part that matters.** Four terminals in one cafe lose wifi at
the same instant, because they share one router. Without jitter they retry in
perfect lockstep and hammer the server the moment it returns — the thundering
herd that turns a brief outage into a long one. A ±20% spread is enough to
scatter four clients across a window wider than the request itself.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

BASE_SECONDS = 2
MAX_SECONDS = 300
JITTER = 0.2


def next_delay(attempts: int, *, rng: random.Random | None = None) -> float:
    """Seconds to wait before attempt number `attempts + 1`."""
    rng = rng or random
    raw = min(BASE_SECONDS * (2 ** max(0, attempts)), MAX_SECONDS)
    spread = raw * JITTER
    return max(0.5, raw + rng.uniform(-spread, spread))


def next_retry_at(attempts: int, *, now: datetime | None = None, rng=None) -> datetime:
    now = now or datetime.now(UTC)
    return now + timedelta(seconds=next_delay(attempts, rng=rng))
