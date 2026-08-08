"""
Reading the audit log.

Read-only by construction: there is no POST, PATCH or DELETE here, and there is
no view class that could grow one by accident. The write path is
`services.record`, called from the code that performs the action.

Scoping is by ORGANIZATION rather than branch, because a Super Admin
investigating something needs to see it wherever it happened — but a caller with
a branch selected sees only that branch, so a branch manager cannot read another
branch's trail.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import models
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError, NotFoundError

from .models import AuditLog
from .serializers import AuditActionSerializer, AuditLogSerializer, catalogue

MAX_ROWS = 200


class AuditLogListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "audit.view"

    @extend_schema(
        summary="Audit log",
        parameters=[
            OpenApiParameter("action", str),
            OpenApiParameter("domain", str),
            OpenApiParameter("severity", str, enum=["INFO", "NOTICE", "WARNING"]),
            OpenApiParameter("actor", str),
            OpenApiParameter("object_id", str),
            OpenApiParameter("since", str, description="ISO-8601. Defaults to 30 days ago."),
        ],
        responses={200: AuditLogSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        rows = AuditLog.objects.filter(organization_id=principal.organization_id)

        if principal.is_superuser:
            # Org-less rows — a failed login against an address that belongs to
            # no tenant — are a platform signal, not one tenant's business.
            rows = AuditLog.objects.filter(
                models.Q(organization_id=principal.organization_id)
                | models.Q(organization__isnull=True)
            )

        if principal.branch_id is not None:
            # A branch manager reads their own branch. Org-level rows (a licence
            # change, a failed login with no branch) stay visible, because
            # hiding them would make the trail look emptier than it is.
            rows = rows.filter(branch_id__in=[principal.branch_id, None])

        params = request.query_params
        for field in ("action", "domain", "severity", "object_id"):
            if value := params.get(field):
                rows = rows.filter(**{field: value})
        if actor := params.get("actor"):
            rows = rows.filter(actor_id=actor)

        since = params.get("since")
        if since:
            try:
                parsed = datetime.fromisoformat(since)
            except ValueError as exc:
                raise AppError("تاريخ غير صالح", code="INVALID_DATE") from exc
            cutoff = parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
        else:
            cutoff = timezone.now() - timedelta(days=30)

        rows = rows.filter(occurred_at__gte=cutoff).select_related("actor")
        return Response(AuditLogSerializer(rows[:MAX_ROWS], many=True).data)


class AuditLogDetailView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "audit.view"

    @extend_schema(summary="One audit entry", responses={200: AuditLogSerializer})
    def get(self, request: Request, pk: int) -> Response:
        principal = auth_context(request)
        row = (
            AuditLog.objects.filter(id=pk, organization_id=principal.organization_id)
            .select_related("actor")
            .first()
        )

        if row is None:
            raise NotFoundError("السجل غير موجود", code="AUDIT_ENTRY_NOT_FOUND")
        return Response(AuditLogSerializer(row).data)


class AuditActionsView(APIView):
    """The catalogue, so the UI's filter is generated rather than hardcoded."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "audit.view"

    @extend_schema(
        summary="Audited action catalogue", responses={200: AuditActionSerializer(many=True)}
    )
    def get(self, request: Request) -> Response:
        return Response(catalogue())
