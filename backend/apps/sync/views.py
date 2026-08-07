"""
The sync API.

`/sync/push/` and `/sync/pull/` are DEVICE endpoints: they are authorized by the
activated terminal itself, not by a human's permission. That is not laxity — an
outbox has to drain at 3am with nobody logged in, and a terminal that has been
queueing since Tuesday needs to catch up before anyone arrives to notice. What a
device cannot do is act as a person: the actor on every operation is whoever the
POS token says was logged in, and every handler still runs the ordinary,
permission-checked service.

The conflict and status endpoints are the opposite: human-facing, permissioned,
and the reason the whole engine is not silent.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError, NotFoundError
from apps.licensing.models import Device, DeviceStatus
from apps.organizations.models import Branch

from . import services
from .models import Stream, SyncConflict, SyncOperation
from .serializers import (
    BranchStatusSerializer,
    DeviceStatusSerializer,
    PullResponseSerializer,
    PushRequestSerializer,
    PushResponseSerializer,
    ResolveConflictSerializer,
    SyncConflictSerializer,
    SyncOperationSerializer,
)


def _branch(request: Request) -> Branch:
    principal = auth_context(request)
    branch = Branch.objects.filter(id=principal.branch_id).first()
    if branch is None:
        raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED", status_code=400)
    return branch


def _device(request: Request) -> Device:
    principal = auth_context(request)
    if principal.device_id is None:
        raise AppError("هذا الإجراء متاح من جهاز مفعّل فقط", code="DEVICE_REQUIRED")

    device = Device.objects.select_related("branch").filter(id=principal.device_id).first()
    if device is None:
        raise NotFoundError("الجهاز غير موجود", code="DEVICE_NOT_FOUND")
    if device.status == DeviceStatus.REVOKED:
        # A revoked terminal must not be able to keep writing. Its queued sales
        # are recoverable through support; letting it push would defeat the
        # point of revoking it.
        raise AppError("الجهاز موقوف", code="DEVICE_REVOKED", status_code=403)
    return device


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


# ── device endpoints ─────────────────────────────────────────────────────────


class PushView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Push a batch of queued operations",
        request=PushRequestSerializer,
        responses={200: PushResponseSerializer, 207: PushResponseSerializer},
    )
    def post(self, request: Request) -> Response:
        payload = PushRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        device = _device(request)
        result = services.apply_push(
            device=device,
            branch=device.branch,
            operations=payload.validated_data["operations"],
            batch_id=payload.validated_data.get("batch_id"),
            actor=_acting_user(request),
        )

        # 207 when the batch is mixed, so a client can tell "everything landed"
        # from "most of it did" without parsing every result.
        return Response(
            {"applied": result.applied, "failed": result.failed, "results": result.results},
            status=(status.HTTP_207_MULTI_STATUS if result.is_mixed else status.HTTP_200_OK),
        )


class PullView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Pull server changes on one stream",
        parameters=[
            OpenApiParameter("stream", str, required=True, enum=Stream.values),
            OpenApiParameter("cursor", int, description="Last seq this device processed."),
            OpenApiParameter("limit", int),
        ],
        responses={200: PullResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        device = _device(request)
        branch = device.branch

        context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
        default_limit = resolver.get("sync.pull_page_size", context)

        try:
            cursor = int(request.query_params.get("cursor", 0))
            limit = min(int(request.query_params.get("limit", default_limit)), 2000)
        except ValueError as exc:
            raise AppError("قيمة غير رقمية", code="INVALID_PARAMETER") from exc

        device.last_seen_at = timezone.now()
        device.save(update_fields=["last_seen_at"])

        return Response(
            services.pull(
                branch=branch,
                stream=request.query_params.get("stream", Stream.CATALOG),
                cursor=cursor,
                limit=limit,
                device=device,
            )
        )


class SyncStateView(APIView):
    """
    Where this device stands, from the server's point of view.

    A terminal asks this on startup so it can show an honest indicator instead
    of assuming it is up to date because nothing has failed yet.
    """

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(summary="This device's sync state", responses={200: DeviceStatusSerializer})
    def get(self, request: Request) -> Response:
        device = _device(request)
        return Response(
            {
                **services.device_status(device),
                "heads": {
                    s: services.changelog.head(branch_id=device.branch_id, stream=s)
                    for s in Stream.values
                },
                "server_time": timezone.now().isoformat(),
            }
        )


# ── admin endpoints ──────────────────────────────────────────────────────────


class BranchSyncStatusView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "sync.view"

    @extend_schema(
        summary="Sync health for the whole branch", responses={200: BranchStatusSerializer}
    )
    def get(self, request: Request) -> Response:
        return Response(services.branch_status(_branch(request)))


class ConflictListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "sync.view"

    @extend_schema(
        summary="Sync conflicts",
        parameters=[OpenApiParameter("resolved", bool)],
        responses={200: SyncConflictSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        conflicts = SyncConflict.objects.filter(branch=_branch(request)).select_related(
            "operation", "operation__device"
        )
        if request.query_params.get("resolved") != "true":
            conflicts = conflicts.filter(resolved_at__isnull=True)

        return Response(SyncConflictSerializer(conflicts[:200], many=True).data)


class ConflictResolveView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "sync.resolve_conflicts"

    @extend_schema(
        summary="Resolve a sync conflict",
        request=ResolveConflictSerializer,
        responses={200: SyncConflictSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        payload = ResolveConflictSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        conflict = (
            SyncConflict.objects.filter(id=pk, branch=_branch(request))
            .select_related("operation")
            .first()
        )
        if conflict is None:
            raise NotFoundError("التعارض غير موجود", code="CONFLICT_NOT_FOUND")

        resolved = services.resolve_conflict(
            conflict,
            resolution=payload.validated_data["resolution"],
            note=payload.validated_data.get("note", ""),
            user=_acting_user(request),
        )
        return Response(SyncConflictSerializer(resolved).data)


class OperationListView(APIView):
    """The push log — what each terminal actually sent, and what became of it."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "sync.view"

    @extend_schema(
        summary="Pushed operations",
        parameters=[OpenApiParameter("status", str), OpenApiParameter("device", str)],
        responses={200: SyncOperationSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        operations = SyncOperation.objects.filter(branch=_branch(request))

        if status_filter := request.query_params.get("status"):
            operations = operations.filter(status=status_filter)
        if device_id := request.query_params.get("device"):
            operations = operations.filter(device_id=device_id)

        return Response(
            SyncOperationSerializer(operations.order_by("-received_at")[:200], many=True).data
        )
