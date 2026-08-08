"""
The sync engine: drain the outbox, pull the mirror, report honestly.

A sync engine that fails silently is worse than none — staff keep working,
confident everything is recorded, and discover a week later that this terminal
has been queueing since Tuesday. Every failure mode here is designed to be loud:
the header shows the queue depth, a conflict raises an indicator, and a rejected
operation never disappears into a retry loop.
"""

from .backoff import next_delay  # noqa: F401
from .engine import SyncEngine, SyncStatus  # noqa: F401
