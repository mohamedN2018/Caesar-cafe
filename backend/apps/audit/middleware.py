"""
Populating the audit context for the duration of a request.

Runs AFTER `AuthContextMiddleware`, because it reads the resolved principal.
Ordering in `MIDDLEWARE` matters and is asserted by a test — a silent reorder
would produce audit rows with no actor, which is the shape you notice only
during a dispute.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from . import context as audit_context


class AuditContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        principal = getattr(request, "auth_context", None)

        token = audit_context.set_context(
            audit_context.AuditContext(
                actor_id=str(principal.user_id) if principal and principal.user_id else None,
                organization_id=(
                    str(principal.organization_id)
                    if principal and principal.organization_id
                    else None
                ),
                branch_id=str(principal.branch_id) if principal and principal.branch_id else None,
                device_id=str(principal.device_id) if principal and principal.device_id else None,
                ip_address=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:250],
                request_id=getattr(request, "request_id", "") or "",
            )
        )
        try:
            return self.get_response(request)
        finally:
            audit_context.reset(token)


def _client_ip(request: HttpRequest) -> str | None:
    """
    The left-most entry in `X-Forwarded-For`, which is the client the proxy saw.

    Trusting this header is only safe because the only thing that can reach the
    app is our own reverse proxy — Postgres, Redis and the app are on an
    internal-only network with no published ports (docs/09, I6).
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None
