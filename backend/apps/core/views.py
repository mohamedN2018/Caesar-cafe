"""System endpoints: health and version. Both public and unauthenticated."""

from __future__ import annotations

from django.conf import settings
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import HealthSerializer, SystemInfoSerializer


class HealthView(APIView):
    """
    Liveness + dependency check.

    Public by design — the container healthcheck and any uptime monitor need it
    without credentials. It exposes no business data.
    """

    permission_classes: list = []
    authentication_classes: list = []
    throttle_classes: list = []
    required_permission = ""

    @extend_schema(
        summary="Health check",
        description="Reports API liveness and database reachability.",
        responses={200: HealthSerializer, 503: None},
    )
    def get(self, request: Request) -> Response:
        checks = {"database": self._check_database()}
        healthy = all(checks.values())

        if healthy:
            return Response(
                {
                    "status": "healthy",
                    "version": settings.APP_VERSION,
                    "checks": checks,
                }
            )

        # Build the error envelope explicitly. Letting the renderer coerce a
        # degraded body would file the check results under `errors`, which is
        # documented as per-field validation messages.
        return Response(
            {
                "success": False,
                "message": "الخدمة غير متاحة مؤقتاً",
                "code": "SERVICE_DEGRADED",
                "errors": {},
                "detail": {"version": settings.APP_VERSION, "checks": checks},
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _check_database() -> bool:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            return False
        return True


class SystemInfoView(APIView):
    """
    Version negotiation for Desktop clients (§61).

    A client below `min_supported_client_version` is refused everything except
    the heartbeat — one that predates a breaking sync change must not be allowed
    to corrupt data by guessing at the new contract.
    """

    permission_classes: list = []
    authentication_classes: list = []
    required_permission = ""

    @extend_schema(
        summary="Server and client version information",
        responses={200: SystemInfoSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(
            {
                "server_version": settings.APP_VERSION,
                "min_supported_client_version": settings.MIN_SUPPORTED_CLIENT_VERSION,
                "api_version": "v1",
                "demo_mode": settings.DEMO_MODE,
                "demo_accounts": _demo_accounts(),
            }
        )


#: The demo staff, mirroring `seed_demo.STAFF`.
#:
#: Duplicated rather than imported: a management command is not an import target for
#: a view, and pulling one in would drag the whole seeding module into every
#: request's import graph. `test_demo_accounts.py` asserts the two lists agree, so
#: the copy cannot drift silently.
_DEMO_STAFF = [
    ("owner@caesar.test", "محمد القيصر", "مالك", "1111"),
    ("manager@caesar.test", "أحمد عبد الرحمن", "مدير فرع", "2222"),
    ("cashier@caesar.test", "منى سعيد", "كاشير", "3333"),
    ("cashier2@caesar.test", "كريم فؤاد", "كاشير", "3344"),
    ("waiter@caesar.test", "يوسف طارق", "ويتر", "4444"),
    ("waiter2@caesar.test", "عمر حسن", "ويتر", "4455"),
    ("kitchen@caesar.test", "سيد الشيف", "مطبخ", "5555"),
    ("kids@caesar.test", "سارة إبراهيم", "منطقة أطفال", "6666"),
    ("store@caesar.test", "حسام أمين", "مسؤول مخزن", "7777"),
    ("accountant@caesar.test", "نهى مصطفى", "محاسب", "8888"),
]

_DEMO_PASSWORD = "caesar-demo-2026"  # noqa: S105 — a demo credential, published on purpose


def _demo_accounts() -> list[dict[str, str]]:
    """
    The demo logins, for the sign-in screen to offer.

    Two gates, both required, because this endpoint takes no authentication and a
    list of working credentials is the worst possible thing to leak from one:

      1. `DEMO_MODE` must be explicitly on. Default off.
      2. The account must actually exist. A stale entry for a user somebody deleted
         is a login that fails — and a demo whose own buttons do not work is worse
         than one that makes you type.

    The database check is second so the query is skipped entirely on a real install
    rather than running on every unauthenticated request.
    """
    if not settings.DEMO_MODE:
        return []

    from apps.accounts.models import User

    known = {email for email, _, _, _ in _DEMO_STAFF}
    live = set(
        User.objects.filter(email__in=known, is_active=True).values_list("email", flat=True)
    )

    accounts = [
        {"email": email, "password": _DEMO_PASSWORD, "name": name, "role": role, "pin": pin}
        for email, name, role, pin in _DEMO_STAFF
        if email in live
    ]

    # The superuser from `demo_admin`, whose password this code does not know — it
    # is set by that command and can be rotated with `--rotate`. Offered without a
    # password rather than with a guess, so a rotated one cannot be shown as valid.
    admin = (
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email__in=known)
        .order_by("created_at")
        .first()
    )
    if admin:
        accounts.insert(
            0,
            {
                "email": admin.email,
                "password": "",
                "name": "مدير النظام",
                "role": "مدير عام",
                "pin": "",
            },
        )

    return accounts
