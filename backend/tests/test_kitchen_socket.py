"""
The WebSocket layer, driven through a real Channels communicator.

The security property that matters most: an unauthenticated or cross-branch
socket is closed BEFORE it joins any group, so it never receives a single
message. Checking later would leave a window in which a stranger is subscribed
to a branch's order traffic.

All database setup happens in SYNC fixtures — pytest runs those outside the
event loop, so the ORM is never touched from async code.
"""

from __future__ import annotations

import uuid

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from apps.accounts import tokens
from apps.kitchen.consumers import CLOSE_FORBIDDEN, CLOSE_UNAUTHENTICATED, BranchConsumer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _close_consumer_connections():
    """
    Close the connections the consumer's `database_sync_to_async` threads open.

    Without this the test database cannot be dropped at teardown — those threads
    outlive the test and Postgres refuses to drop a database with live sessions.
    """
    yield
    from django.db import connections

    connections.close_all()


def _token(user, branch) -> str:
    return tokens.issue_pair(
        user=user,
        kind="WEB",
        organization_id=user.organization_id,
        branch_id=branch.id,
    )["access"]


@pytest.fixture
def kitchen_token(branch, make_user) -> str:
    return _token(make_user(role="KITCHEN", branch=branch), branch)


@pytest.fixture
def waiter_token(branch, make_user) -> str:
    return _token(make_user(email="waiter@caesar.test", role="WAITER", branch=branch), branch)


def connect_to(branch_id, token: str | None, *, station: str | None = None):
    """Build a communicator with the url_route Channels would populate."""
    query = f"token={token}" if token else ""
    if station:
        query += f"&station={station}"

    communicator = WebsocketCommunicator(
        BranchConsumer.as_asgi(), f"/ws/branch/{branch_id}/?{query}"
    )
    communicator.scope["url_route"] = {"kwargs": {"branch_id": str(branch_id)}}
    return communicator


class TestAuthentication:
    async def test_no_token_is_closed(self, branch) -> None:
        connected, code = await connect_to(branch.id, None).connect()
        assert connected is False
        assert code == CLOSE_UNAUTHENTICATED

    async def test_a_garbage_token_is_closed(self, branch) -> None:
        connected, code = await connect_to(branch.id, "not.a.token").connect()
        assert connected is False
        assert code == CLOSE_UNAUTHENTICATED

    async def test_a_valid_token_connects(self, branch, kitchen_token) -> None:
        communicator = connect_to(branch.id, kitchen_token)
        connected, _ = await communicator.connect()
        assert connected is True

        hello = await communicator.receive_json_from(timeout=5)
        assert hello["event"] == "connected"
        await communicator.disconnect()

    async def test_a_socket_cannot_subscribe_to_another_branch(
        self, other_branch, kitchen_token
    ) -> None:
        """Threat I1, over a protocol CORS does not cover."""
        communicator = connect_to(other_branch.id, kitchen_token)
        connected, code = await communicator.connect()

        assert connected is False
        assert code == CLOSE_FORBIDDEN


class TestSubscriptions:
    async def test_kitchen_staff_join_the_kitchen_feed(self, branch, kitchen_token) -> None:
        communicator = connect_to(branch.id, kitchen_token)
        await communicator.connect()
        hello = await communicator.receive_json_from(timeout=5)

        assert f"branch.{branch.id}.kitchen" in hello["channels"]
        await communicator.disconnect()

    async def test_a_station_filter_adds_its_own_channel(self, branch, kitchen_token) -> None:
        station = uuid.uuid4()
        communicator = connect_to(branch.id, kitchen_token, station=str(station))
        await communicator.connect()
        hello = await communicator.receive_json_from(timeout=5)

        assert f"branch.{branch.id}.station.{station}" in hello["channels"]
        await communicator.disconnect()

    async def test_every_channel_is_scoped_to_the_branch(self, branch, waiter_token) -> None:
        """Subscriptions are derived from permissions, never granted wholesale."""
        communicator = connect_to(branch.id, waiter_token)
        await communicator.connect()
        hello = await communicator.receive_json_from(timeout=5)

        assert hello["channels"]
        assert all(channel.startswith(f"branch.{branch.id}.") for channel in hello["channels"])
        await communicator.disconnect()


class TestDelivery:
    async def test_a_broadcast_reaches_the_kitchen(self, branch, kitchen_token) -> None:
        communicator = connect_to(branch.id, kitchen_token)
        await communicator.connect()
        await communicator.receive_json_from(timeout=5)  # the hello frame

        await get_channel_layer().group_send(
            f"branch.{branch.id}.kitchen",
            {
                "type": "kitchen.event",
                "event": "ticket.created",
                "ticket": {"id": str(uuid.uuid4()), "ticket_number": 7, "status": "NEW"},
            },
        )

        message = await communicator.receive_json_from(timeout=5)
        assert message["event"] == "ticket.created"
        assert message["ticket"]["ticket_number"] == 7
        await communicator.disconnect()

    async def test_another_branch_broadcast_is_not_delivered(
        self, branch, other_branch, kitchen_token
    ) -> None:
        communicator = connect_to(branch.id, kitchen_token)
        await communicator.connect()
        await communicator.receive_json_from(timeout=5)

        await get_channel_layer().group_send(
            f"branch.{other_branch.id}.kitchen",
            {"type": "kitchen.event", "event": "ticket.created", "ticket": {"id": "x"}},
        )

        assert await communicator.receive_nothing(timeout=1) is True
        await communicator.disconnect()

    async def test_the_socket_accepts_no_state_changes(self, branch, kitchen_token) -> None:
        """
        Mutations go through REST so they get the same permission checks,
        validation and audit trail. A second, weaker door into the same rooms is
        exactly what this avoids.
        """
        communicator = connect_to(branch.id, kitchen_token)
        await communicator.connect()
        await communicator.receive_json_from(timeout=5)

        await communicator.send_json_to({"type": "ticket.ready", "ticket_id": str(uuid.uuid4())})
        await communicator.send_json_to({"type": "ping"})

        # Only the ping is answered; the mutation is ignored entirely.
        assert await communicator.receive_json_from(timeout=5) == {"event": "pong"}
        await communicator.disconnect()
