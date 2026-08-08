"""
The sync engine.

The properties that matter, in order of what they would cost to get wrong:

  * an outage NEVER discards a sale — it defers it;
  * a batch is not lost because one operation in it was rejected;
  * the cursor advances only as far as the rows actually applied;
  * a revoked permission does not survive in the mirror;
  * the status indicator never claims to be online when it is not.

The HTTP layer is stubbed at the transport, not the engine: mocking the engine's
own boundaries would test the mock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from caesar_pos.api.client import ApiClient
from caesar_pos.local import outbox
from caesar_pos.local.db import Database, connect, transaction
from caesar_pos.sync import puller, pusher
from caesar_pos.sync.backoff import next_delay
from caesar_pos.sync.engine import State, SyncEngine


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "sync.db")
    yield Database(connection)
    connection.close()


def envelope(data: dict, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"success": 200 <= status < 300, "data": data})


def failure(code: str, *, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"success": False, "code": code, "message": code})


def client_with(handler) -> ApiClient:
    """An ApiClient whose transport is a function, so no network is involved."""
    client = ApiClient(base_url="http://server")
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    return client


def queue(db: Database, count: int = 1, entity_type: str = "order_event") -> list[str]:
    ids = []
    for index in range(count):
        with transaction(db.connection):
            ids.append(
                outbox.enqueue(
                    db, entity_type=entity_type, payload={"n": index}, entity_id="order-1"
                )
            )
    return ids


# ── backoff ──────────────────────────────────────────────────────────────────


class TestBackoff:
    def test_it_doubles_and_caps_at_five_minutes(self) -> None:
        import random

        # Seeded for determinism. Retry jitter is scheduling, not secrecy.
        rng = random.Random(1)  # noqa: S311
        assert 1.5 < next_delay(0, rng=rng) < 2.5
        assert 3.0 < next_delay(1, rng=rng) < 5.0
        assert next_delay(20, rng=rng) <= 360

    def test_jitter_scatters_simultaneous_retries(self) -> None:
        """
        Four terminals in one cafe share one router and lose wifi together.
        Without jitter they retry in lockstep and hammer the server the moment it
        returns — the thundering herd that turns a brief outage into a long one.
        """
        delays = {round(next_delay(4), 4) for _ in range(20)}
        assert len(delays) > 15, "the delays are not being spread"

    def test_it_never_returns_zero(self) -> None:
        assert all(next_delay(0) >= 0.5 for _ in range(50))


# ── push ─────────────────────────────────────────────────────────────────────


class TestPush:
    def test_an_applied_batch_clears_the_queue(self, db) -> None:
        ids = queue(db, 3)

        def handler(request):
            body = json.loads(request.content)
            assert len(body["operations"]) == 3
            return envelope(
                {
                    "applied": 3,
                    "failed": 0,
                    "results": [
                        {"op_uuid": op["op_uuid"], "status": "APPLIED", "result": {}}
                        for op in body["operations"]
                    ],
                }
            )

        outcome = pusher.drain_once(db, client_with(handler))

        assert outcome.applied == 3
        assert outbox.pending(db) == []
        assert all(
            db.scalar("SELECT status FROM sync_outbox WHERE op_uuid = ?", (i,)) == "SYNCED"
            for i in ids
        )

    def test_an_outage_defers_and_never_discards(self, db) -> None:
        """The whole point: a queue that survives a week of bad wifi."""
        queue(db, 5)

        def handler(request):
            raise httpx.ConnectError("no route to host")

        outcome = pusher.drain_once(db, client_with(handler))

        assert outcome.offline is True
        assert outcome.deferred == 5
        assert outbox.counts(db)["pending"] == 5, "still queued"
        assert outbox.counts(db)["rejected"] == 0, "an outage is not a rejection"

    def test_a_deferred_operation_backs_off_before_the_next_attempt(self, db) -> None:
        queue(db, 1)

        def handler(request):
            raise httpx.ConnectError("down")

        pusher.drain_once(db, client_with(handler))

        assert outbox.pending(db) == [], "backing off"
        assert len(outbox.pending(db, now=datetime.now(UTC) + timedelta(minutes=10))) == 1

    def test_one_rejection_does_not_lose_the_rest(self, db) -> None:
        """
        docs/07: 49 applied, 1 rejected, batch not blocked. An all-or-nothing
        batch means a single poisoned row stalls the terminal forever.
        """
        queue(db, 3)

        def handler(request):
            ops = json.loads(request.content)["operations"]
            results = [
                {"op_uuid": ops[0]["op_uuid"], "status": "APPLIED", "result": {}},
                {"op_uuid": ops[1]["op_uuid"], "status": "REJECTED", "code": "BAD"},
                {"op_uuid": ops[2]["op_uuid"], "status": "APPLIED", "result": {}},
            ]
            return envelope({"applied": 2, "failed": 1, "results": results}, status=207)

        outcome = pusher.drain_once(db, client_with(handler))

        assert outcome.applied == 2
        assert outcome.rejected == 1
        assert outbox.pending(db) == []

    def test_a_conflict_becomes_visible_instead_of_retrying_forever(self, db) -> None:
        queue(db, 1)

        def handler(request):
            op = json.loads(request.content)["operations"][0]
            return envelope(
                {
                    "applied": 0,
                    "failed": 1,
                    "results": [
                        {
                            "op_uuid": op["op_uuid"],
                            "status": "CONFLICT",
                            "code": "ORDER_ALREADY_CLOSED",
                            "server_state": {"local_number": "MB-01-0042"},
                        }
                    ],
                }
            )

        outcome = pusher.drain_once(db, client_with(handler))

        assert outcome.conflicts == 1
        conflicts = outbox.open_conflicts(db)
        assert conflicts[0]["server_state"]["local_number"] == "MB-01-0042"

    def test_an_operation_the_server_did_not_mention_is_retried(self, db) -> None:
        """
        Not an error and not a success. Sending it again is safe — that is what
        the op_uuid is for — and assuming either outcome is not.
        """
        queue(db, 2)

        def handler(request):
            ops = json.loads(request.content)["operations"]
            return envelope(
                {
                    "applied": 1,
                    "failed": 0,
                    "results": [{"op_uuid": ops[0]["op_uuid"], "status": "APPLIED"}],
                }
            )

        outcome = pusher.drain_once(db, client_with(handler))

        assert outcome.applied == 1
        assert outcome.deferred == 1
        assert outbox.counts(db)["pending"] == 1

    def test_a_401_defers_immediately_for_a_token_refresh(self, db) -> None:
        """An expired device token mid-shift is ordinary — not a reason to wait."""
        queue(db, 2)
        outcome = pusher.drain_once(db, client_with(lambda r: failure("TOKEN_EXPIRED", status=401)))

        assert outcome.deferred == 2
        assert len(outbox.pending(db)) == 2, "retryable right away"

    def test_a_client_side_batch_error_stalls_rather_than_discarding(self, db) -> None:
        """
        Rejecting fifty real sales because the batch envelope was malformed would
        be far worse than a queue that stops until somebody looks at it.
        """
        queue(db, 3)
        outcome = pusher.drain_once(
            db, client_with(lambda r: failure("VALIDATION_ERROR", status=422))
        )

        assert outcome.deferred == 3
        assert outbox.counts(db)["rejected"] == 0

    def test_an_empty_queue_makes_no_request(self, db) -> None:
        called = []

        def handler(request):
            called.append(request)
            return envelope({})

        assert pusher.drain_once(db, client_with(handler)).attempted == 0
        assert called == []

    def test_an_order_is_stamped_synced_only_when_everything_landed(self, db) -> None:
        from caesar_pos.local.db import transaction as tx

        with tx(db.connection):
            db.insert(
                "l_orders",
                {
                    "id": "order-1",
                    "local_number": "MB-01-0001",
                    "opened_at": datetime.now(UTC).isoformat(),
                },
            )
        queue(db, 2)

        def partial(request):
            ops = json.loads(request.content)["operations"]
            return envelope(
                {
                    "applied": 1,
                    "failed": 0,
                    "results": [{"op_uuid": ops[0]["op_uuid"], "status": "APPLIED"}],
                }
            )

        pusher.drain_once(db, client_with(partial))
        assert db.scalar("SELECT synced_at FROM l_orders WHERE id = 'order-1'") is None

        # The unmentioned operation is backing off, so drive the clock forward.
        later = datetime.now(UTC) + timedelta(minutes=10)

        def rest(request):
            ops = json.loads(request.content)["operations"]
            return envelope(
                {
                    "applied": len(ops),
                    "failed": 0,
                    "results": [{"op_uuid": op["op_uuid"], "status": "APPLIED"} for op in ops],
                }
            )

        pusher.drain_once(db, client_with(rest), now=later)
        assert db.scalar("SELECT synced_at FROM l_orders WHERE id = 'order-1'") is not None


# ── pull ─────────────────────────────────────────────────────────────────────


class TestPull:
    def _catalog(self, changes: list[dict], *, cursor: int = 10, has_more: bool = False):
        def handler(request):
            return envelope(
                {"stream": "catalog", "cursor": cursor, "has_more": has_more, "changes": changes}
            )

        return handler

    def test_a_product_lands_in_the_mirror(self, db) -> None:
        handler = self._catalog(
            [
                {
                    "seq": 1,
                    "entity_type": "product",
                    "entity_id": "p1",
                    "operation": "UPSERT",
                    "payload": {"name_ar": "كابتشينو", "sku": "CAPP", "is_active": True},
                }
            ]
        )
        outcome = puller.pull_stream(db, client_with(handler), "catalog")

        assert outcome.applied == 1
        assert db.scalar("SELECT name_ar FROM m_products WHERE id = 'p1'") == "كابتشينو"

    def test_the_cursor_is_stored_and_reused(self, db) -> None:
        seen = []

        def handler(request):
            seen.append(dict(request.url.params))
            return envelope({"cursor": 48731, "has_more": False, "changes": []})

        puller.pull_stream(db, client_with(handler), "catalog")
        puller.pull_stream(db, client_with(handler), "catalog")

        assert seen[0]["cursor"] == "0"
        assert seen[1]["cursor"] == "48731"

    def test_a_price_change_replaces_rather_than_appending(self, db) -> None:
        for price in ("60.00", "70.00"):
            puller.pull_stream(
                db,
                client_with(
                    self._catalog(
                        [
                            {
                                "entity_type": "variant",
                                "entity_id": "v1",
                                "operation": "UPSERT",
                                "payload": {"product_id": "p1", "price": price},
                            }
                        ]
                    )
                ),
                "catalog",
            )

        assert db.scalar("SELECT COUNT(*) FROM m_variants") == 1
        assert db.scalar("SELECT price FROM m_variants WHERE id = 'v1'") == "70.00"

    def test_a_delete_removes_the_row(self, db) -> None:
        db.upsert_mirror("m_tables", {"id": "t1", "area_id": "a", "number": "5", "payload": "{}"})
        puller.pull_stream(
            db,
            client_with(
                self._catalog(
                    [
                        {
                            "entity_type": "table",
                            "entity_id": "t1",
                            "operation": "DELETE",
                            "payload": {},
                        }
                    ]
                )
            ),
            "floor",
        )

        assert db.scalar("SELECT COUNT(*) FROM m_tables") == 0

    def test_a_revoked_permission_does_not_survive_in_the_cache(self, db) -> None:
        """
        The one mirror update that is a security control: a manager removed from
        the system must lose their step-up authority on this terminal too.
        """
        puller.pull_stream(
            db,
            client_with(
                self._catalog(
                    [
                        {
                            "entity_type": "role_assignment",
                            "entity_id": "ra1",
                            "operation": "UPSERT",
                            "payload": {
                                "user_id": "u1",
                                "role_code": "BRANCH_MANAGER",
                                "permissions": ["orders.refund"],
                            },
                        }
                    ]
                )
            ),
            "staff",
        )
        assert db.scalar("SELECT COUNT(*) FROM m_permissions") == 1

        puller.pull_stream(
            db,
            client_with(
                self._catalog(
                    [
                        {
                            "entity_type": "role_assignment",
                            "entity_id": "ra1",
                            "operation": "DELETE",
                            "payload": {},
                        }
                    ]
                )
            ),
            "staff",
        )
        assert db.scalar("SELECT COUNT(*) FROM m_permissions") == 0

    def test_an_unknown_entity_type_is_skipped_not_fatal(self, db) -> None:
        """
        A server newer than this client. A terminal that refused to sync over a
        feature it does not have is a terminal that stops selling.
        """
        outcome = puller.pull_stream(
            db,
            client_with(
                self._catalog(
                    [
                        {
                            "entity_type": "loyalty_tier",
                            "entity_id": "x",
                            "operation": "UPSERT",
                            "payload": {},
                        },
                        {
                            "entity_type": "product",
                            "entity_id": "p1",
                            "operation": "UPSERT",
                            "payload": {"name_ar": "شاي"},
                        },
                    ]
                )
            ),
            "catalog",
        )

        assert outcome.applied == 1
        assert outcome.cursor == 10, "the cursor still advances past what was skipped"

    def test_an_outage_leaves_the_cursor_where_it_was(self, db) -> None:
        def handler(request):
            raise httpx.ConnectError("down")

        outcome = puller.pull_stream(db, client_with(handler), "catalog")

        assert outcome.offline is True
        assert db.scalar("SELECT cursor FROM m_sync_meta WHERE stream = 'catalog'") is None

    def test_a_setting_is_stored_by_key(self, db) -> None:
        puller.pull_stream(
            db,
            client_with(
                self._catalog(
                    [
                        {
                            "entity_type": "setting",
                            "entity_id": "b1",
                            "operation": "UPSERT",
                            "payload": {"key": "finance.vat_percent", "value": "14.00"},
                        }
                    ]
                )
            ),
            "config",
        )

        assert (
            json.loads(db.scalar("SELECT value FROM m_settings WHERE key = 'finance.vat_percent'"))
            == "14.00"
        )

    def test_an_order_event_from_another_device_is_stored_once(self, db) -> None:
        change = {
            "entity_type": "order_event",
            "entity_id": "e1",
            "operation": "UPSERT",
            "payload": {
                "id": "e1",
                "order_id": "o1",
                "sequence": 1,
                "event_type": "ITEM_ADDED",
                "payload": {},
            },
        }

        puller.pull_stream(db, client_with(self._catalog([change])), "orders")
        puller.pull_stream(db, client_with(self._catalog([change])), "orders")

        assert db.scalar("SELECT COUNT(*) FROM l_order_events") == 1


# ── the engine ───────────────────────────────────────────────────────────────


class TestEngine:
    def test_it_reports_online_when_the_queue_is_empty(self, db) -> None:
        engine = SyncEngine(db, client_with(lambda r: envelope({"cursor": 0, "changes": []})))
        status = engine.tick()

        assert status.state == State.ONLINE
        assert status.icon == "🟢"
        assert status.label_ar == "متصل"

    def test_it_reports_offline_with_the_queue_depth(self, db) -> None:
        queue(db, 45)

        def handler(request):
            raise httpx.ConnectError("down")

        status = SyncEngine(db, client_with(handler)).tick()

        assert status.state == State.OFFLINE
        assert status.icon == "🔴"
        assert "45" in status.label_ar

    def test_a_conflict_outranks_everything_in_the_indicator(self, db) -> None:
        """It is the one state that needs a person, so it must not be hidden
        behind a green tick."""
        op_uuid = queue(db, 1)[0]
        outbox.mark_conflict(db, op_uuid, code="SEQUENCE_GAP", server_state={})

        status = SyncEngine(
            db, client_with(lambda r: envelope({"cursor": 0, "changes": []}))
        ).tick()

        assert status.state == State.CONFLICT
        assert status.icon == "⚠️"
        assert "تعارض" in status.label_ar

    def test_it_recovers_when_the_server_returns(self, db) -> None:
        queue(db, 1)
        state = {"down": True}

        def handler(request):
            if state["down"]:
                raise httpx.ConnectError("down")
            if "push" in str(request.url):
                ops = json.loads(request.content)["operations"]
                return envelope(
                    {
                        "applied": len(ops),
                        "failed": 0,
                        "results": [{"op_uuid": o["op_uuid"], "status": "APPLIED"} for o in ops],
                    }
                )
            return envelope({"cursor": 1, "has_more": False, "changes": []})

        engine = SyncEngine(db, client_with(handler))
        assert engine.tick().state == State.OFFLINE

        state["down"] = False
        # The deferred operation is backing off, so drive the clock forward.
        later = datetime.now(UTC) + timedelta(minutes=10)
        assert engine.tick(now=later).state == State.ONLINE
        assert engine.last_success_at is not None

    def test_streams_are_pulled_at_their_own_cadence(self, db) -> None:
        """
        A price list may be a minute old; another terminal's open table may not.
        `orders` polls at 10s, `staff` at 5 minutes.
        """
        pulled = []

        def handler(request):
            if "pull" in str(request.url):
                pulled.append(request.url.params["stream"])
            return envelope({"cursor": 1, "has_more": False, "changes": []})

        engine = SyncEngine(db, client_with(handler))
        start = datetime.now(UTC)

        engine.tick(now=start)
        assert set(pulled) == set(puller.STREAMS), "everything is due on the first tick"

        pulled.clear()
        engine.tick(now=start + timedelta(seconds=15))
        assert pulled == ["orders"], "only the 10s stream is due again"

    def test_bootstrap_pulls_every_stream_to_completion(self, db) -> None:
        """
        A terminal with an empty mirror cannot sell — no products, no prices, no
        payment methods.
        """
        pages = {"n": 0}

        def handler(request):
            if "push" in str(request.url):
                return envelope({"applied": 0, "failed": 0, "results": []})
            pages["n"] += 1
            more = pages["n"] % 2 == 1
            return envelope({"cursor": pages["n"], "has_more": more, "changes": []})

        totals = SyncEngine(db, client_with(handler)).bootstrap()
        assert set(totals) == set(puller.STREAMS)

    def test_a_rejection_still_proves_the_link_is_up(self, db) -> None:
        """A rejection IS a reply — reporting offline after one would be a lie."""
        queue(db, 1)

        def handler(request):
            if "pull" in str(request.url):
                return envelope({"cursor": 0, "has_more": False, "changes": []})
            ops = json.loads(request.content)["operations"]
            return envelope(
                {
                    "applied": 0,
                    "failed": 1,
                    "results": [
                        {"op_uuid": ops[0]["op_uuid"], "status": "REJECTED", "code": "BAD"}
                    ],
                }
            )

        engine = SyncEngine(db, client_with(handler))
        engine.online = False
        engine.tick()

        assert engine.online is True
