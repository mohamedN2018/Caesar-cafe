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
            }
        )
