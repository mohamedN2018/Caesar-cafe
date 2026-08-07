"""
Resolves the authenticated principal once per request and attaches it as
`request.auth_context`.

Doing this in middleware rather than per-view means the tenant scope and the
permission set are established before any queryset is built — there is no window
in which a view could read data before knowing who is asking.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

from django.http import HttpRequest, HttpResponse

from .context import ANONYMOUS, AuthContext, PrincipalKind

logger = logging.getLogger(__name__)


def _as_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


class AuthContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.auth_context = self._resolve(request)  # type: ignore[attr-defined]
        return self.get_response(request)

    def _resolve(self, request: HttpRequest) -> AuthContext:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return ANONYMOUS

        token = header.removeprefix("Bearer ").strip()
        if not token:
            return ANONYMOUS

        # Imported lazily: this middleware loads before the app registry is ready.
        from apps.accounts.models import User
        from apps.accounts.tokens import TokenError, decode
        from apps.authz.services import effective_permissions

        try:
            payload = decode(token, expected_type="access")
        except TokenError:
            # An invalid token is anonymous, not an error. The permission layer
            # decides whether anonymous is acceptable for this route, which
            # keeps 401-vs-403 in one place.
            return ANONYMOUS

        kind = PrincipalKind(payload.get("kind", PrincipalKind.WEB))
        branch_id = _as_uuid(payload.get("branch"))
        device_id = _as_uuid(payload.get("device"))
        user_id = _as_uuid(payload.get("sub"))

        if user_id is None:
            # A device principal: no human, therefore no permissions. It can
            # sync and read, but anything with financial consequence needs a
            # person attached — see RequiresHuman.
            if kind is not PrincipalKind.DEVICE or device_id is None:
                return ANONYMOUS
            return self._device_context(device_id, branch_id)

        user = (
            User.objects.filter(id=user_id, is_active=True)
            .only("id", "organization_id", "is_superuser", "is_active")
            .first()
        )
        if user is None:
            return ANONYMOUS

        return AuthContext(
            kind=kind,
            user_id=user.id,
            organization_id=user.organization_id,
            branch_id=branch_id,
            device_id=device_id,
            permissions=effective_permissions(user.id, branch_id),
            is_superuser=user.is_superuser,
        )

    @staticmethod
    def _device_context(device_id, branch_id) -> AuthContext:
        from apps.licensing.models import Device, DeviceStatus

        device = (
            Device.objects.filter(id=device_id, status=DeviceStatus.ACTIVE)
            .select_related("license")
            .first()
        )
        if device is None:
            # Revoked or suspended mid-session: the token stops working at once,
            # without waiting for it to expire.
            return ANONYMOUS

        return AuthContext(
            kind=PrincipalKind.DEVICE,
            user_id=None,
            organization_id=device.license.organization_id,
            branch_id=branch_id or device.branch_id,
            device_id=device.id,
            permissions=frozenset(),
        )
