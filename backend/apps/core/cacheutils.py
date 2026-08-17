"""
Pattern deletion for the cache Django actually ships.

Three call sites believed they had `cache.delete_pattern(prefix)`. That method is
a **django-redis extension**, and this project uses Django's BUILT-IN
`RedisCache` — which does not have it. So every call raised `AttributeError` and
fell into a fallback whose comment said "LocMemCache (tests)":

    except AttributeError:
        cache.clear()

The comment believed the fallback was a test-only affordance. In production it
ran EVERY time — settings write, role change, permission edit — and `clear()`
against Redis is FLUSHDB: the whole cache, gone. Permission caches, settings
caches, and any coordination key living beside them.

How it surfaced: the demo-data screen guards its rebuild with a lock in the
cache, and the rebuild's own seed writes a setting three seconds in. The
invalidation flushed the lock out from under the running job, a second click
passed the gate, and two seeds interleaved — one deleting the catalogue while
the other sold from it. The lock was correct; the ground it stood on was not.

`SCAN`, never `KEYS`: `KEYS` walks the whole keyspace in one blocking call on
the same Redis that brokers Celery and carries the channel layer.
"""

from __future__ import annotations

from django.core.cache import cache


def delete_pattern(prefix: str) -> int:
    """
    Delete every cache key starting with `prefix`. Returns how many went.

    Works against the built-in Redis backend by scanning the underlying client
    with the cache's own `make_key` versioning, and against LocMem (tests) by
    filtering its dict. The last resort is still `clear()` — but as a named last
    resort for an unknown backend, not a silent everyday code path.
    """
    # ── Django's built-in RedisCache ─────────────────────────────────────────
    inner = getattr(cache, "_cache", None)
    get_client = getattr(inner, "get_client", None)
    if callable(get_client):
        client = get_client(None, write=True)
        match = cache.make_key(prefix) + "*"
        deleted = 0
        batch: list[bytes] = []
        for key in client.scan_iter(match=match, count=500):
            batch.append(key)
            if len(batch) >= 500:
                deleted += client.delete(*batch)
                batch = []
        if batch:
            deleted += client.delete(*batch)
        return deleted

    # ── LocMemCache (tests) ──────────────────────────────────────────────────
    # Its store is a plain dict of fully-made keys. Filtering it beats clear():
    # a test that asserts "unrelated keys survive invalidation" must be able to
    # pass in the very environment tests run in.
    store = getattr(cache, "_cache", None)
    if isinstance(store, dict):
        full = cache.make_key(prefix)
        doomed = [k for k in list(store) if k.startswith(full)]
        for key in doomed:
            store.pop(key, None)
            getattr(cache, "_expire_info", {}).pop(key, None)
        return len(doomed)

    # ── something else entirely ──────────────────────────────────────────────
    cache.clear()
    return -1
