"""
WebSocket consumers for the kitchen display and the POS.

Authenticated in `connect` — an unauthenticated socket is closed BEFORE it joins
any group, so it never receives a single message. Doing the check later would
mean a window in which a stranger is subscribed to a branch's order traffic.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)

CLOSE_UNAUTHENTICATED = 4001
CLOSE_FORBIDDEN = 4003


class BranchConsumer(AsyncJsonWebsocketConsumer):
    """
    One socket per client, subscribed to the channels its role needs.

    Browsers cannot set an Authorization header on a WebSocket, so the token
    arrives as a query parameter. That is standard, and acceptable here because
    the token is short-lived (15 minutes) and the connection is TLS — but it
    does mean the token can land in a proxy access log, which is why device and
    access tokens are separate and neither is long-lived.
    """

    async def connect(self) -> None:
        self.branch_id = self.scope["url_route"]["kwargs"]["branch_id"]
        self.groups_joined: list[str] = []

        token = self._token_from_query()
        if not token:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return

        context = await self._resolve(token)
        if context is None:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return

        # A socket may only ever subscribe to its OWN branch.
        if str(context["branch_id"]) != str(self.branch_id):
            logger.warning(
                "Rejected cross-branch socket",
                extra={"requested": self.branch_id, "actual": context["branch_id"]},
            )
            await self.close(code=CLOSE_FORBIDDEN)
            return

        self.context = context
        await self.accept()

        for group in self._groups_for(context):
            await self.channel_layer.group_add(group, self.channel_name)
            self.groups_joined.append(group)

        await self.send_json({"event": "connected", "channels": self.groups_joined})

    async def disconnect(self, code) -> None:
        for group in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs) -> None:
        """
        The socket is read-mostly.

        State changes go through the REST API so they get the same permission
        checks, validation and audit trail as everything else. Accepting
        mutations here would be a second, weaker door into the same rooms.
        """
        if content.get("type") == "ping":
            await self.send_json({"event": "pong"})

    # ── fan-out handlers ─────────────────────────────────────────────────────

    async def kitchen_event(self, message) -> None:
        await self.send_json({"event": message["event"], "ticket": message["ticket"]})

    async def order_event(self, message) -> None:
        await self.send_json({"event": message["event"], "order": message.get("order")})

    # ── helpers ──────────────────────────────────────────────────────────────

    def _token_from_query(self) -> str | None:
        query = parse_qs(self.scope.get("query_string", b"").decode())
        values = query.get("token")
        return values[0] if values else None

    def _groups_for(self, context) -> list[str]:
        """
        Subscribe only to what this principal needs.

        A floor tablet has no business receiving every kitchen ticket, and the
        kitchen has no business receiving payment events.
        """
        groups: list[str] = []
        permissions = context["permissions"]

        if "kitchen.view" in permissions or context["is_superuser"]:
            groups.append(f"branch.{self.branch_id}.kitchen")
            if station := self._station_from_query():
                groups.append(f"branch.{self.branch_id}.station.{station}")

        if "orders.view" in permissions or context["is_superuser"]:
            groups.append(f"branch.{self.branch_id}.pos")

        if "floor.view" in permissions or context["is_superuser"]:
            groups.append(f"branch.{self.branch_id}.floor")

        return groups

    def _station_from_query(self) -> str | None:
        query = parse_qs(self.scope.get("query_string", b"").decode())
        values = query.get("station")
        return values[0] if values else None

    @database_sync_to_async
    def _resolve(self, token: str):
        from apps.accounts.models import User
        from apps.accounts.tokens import TokenError, decode
        from apps.authz.services import effective_permissions
        from apps.licensing.models import Device, DeviceStatus

        try:
            payload = decode(token, expected_type="access")
        except TokenError:
            return None

        branch_id = payload.get("branch")
        subject = payload.get("sub")

        if subject:
            user = User.objects.filter(id=subject, is_active=True).first()
            if user is None:
                return None
            return {
                "branch_id": branch_id,
                "permissions": set(effective_permissions(user.id, branch_id)),
                "is_superuser": user.is_superuser,
            }

        # A device principal — a kitchen display with no human logged in.
        device_id = payload.get("device")
        device = Device.objects.filter(id=device_id, status=DeviceStatus.ACTIVE).first()
        if device is None:
            return None

        # A KDS-mode device is exactly what this socket exists for, so it gets
        # the kitchen feed without a user attached. It still cannot mutate
        # anything: `receive_json` accepts no state changes.
        return {
            "branch_id": branch_id or str(device.branch_id),
            "permissions": {"kitchen.view"} if device.mode in {"KDS", "BOTH"} else set(),
            "is_superuser": False,
        }
