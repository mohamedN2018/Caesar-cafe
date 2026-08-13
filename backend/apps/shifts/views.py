"""Shift API: open, cash movements, X-report, close."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, RequiresHuman, auth_context
from apps.core.exceptions import AppError, NotFoundError, PermissionDeniedError

from . import services
from .models import Shift, ShiftStatus
from .serializers import (
    CashMovementRequestSerializer,
    CashMovementSerializer,
    CloseShiftSerializer,
    OpenShiftSerializer,
    ShiftReportSerializer,
    ShiftSerializer,
)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _get_shift(request: Request, pk) -> Shift:
    """
    Fetch a shift the caller is entitled to see.

    Reading YOUR OWN shift is part of doing the job — a cashier has to check
    their drawer and close out. `shifts.view_all` is a different capability:
    seeing OTHER people's shifts. Conflating the two would either lock cashiers
    out of their own close-out or hand them everyone's numbers.
    """
    principal = auth_context(request)
    shift = Shift.objects.filter(id=pk, branch_id=principal.branch_id).first()
    if shift is None:
        raise NotFoundError("الوردية غير موجودة", code="SHIFT_NOT_FOUND")

    is_own = shift.user_id == principal.user_id or (
        principal.device_id is not None and shift.device_id == principal.device_id
    )
    if not is_own and not principal.has("shifts.view_all"):
        raise PermissionDeniedError("لا يمكنك عرض وردية مستخدم آخر", code="NOT_YOUR_SHIFT")
    return shift


class ShiftListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "shifts.view_all"

    @extend_schema(summary="List shifts", responses={200: ShiftSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        shifts = Shift.objects.filter(branch_id=principal.branch_id).select_related(
            "user", "closed_by"
        )
        if status_filter := request.query_params.get("status"):
            shifts = shifts.filter(status=status_filter)
        return Response(ShiftSerializer(shifts[:200], many=True).data)


class OpenShiftView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman, HasPermission]
    required_permission = "shifts.open"

    @extend_schema(
        summary="Open a shift", request=OpenShiftSerializer, responses={201: ShiftSerializer}
    )
    def post(self, request: Request) -> Response:
        from apps.organizations.models import Branch

        serializer = OpenShiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        principal = auth_context(request)

        branch = (
            Branch.objects.filter(id=principal.require_branch())
            .select_related("organization")
            .first()
        )
        if branch is None:
            raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED")

        shift = services.open_shift(
            branch=branch,
            user=_acting_user(request),
            device_id=principal.device_id,
            opening_cash=serializer.validated_data["opening_cash"],
        )
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)


class CurrentShiftView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(summary="This device's open shift", responses={200: ShiftSerializer})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        shift = Shift.objects.filter(branch_id=principal.branch_id, status=ShiftStatus.OPEN)
        if principal.device_id:
            shift = shift.filter(device_id=principal.device_id)

        current = shift.select_related("user").first()
        if current is None:
            return Response({"shift": None})
        return Response({"shift": ShiftSerializer(current).data})


class CashMovementView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman, HasPermission]
    # Ownership is enforced in _get_shift; reading your own drawer is the job.
    required_permissions = {"GET": "", "POST": "shifts.cash_movement"}

    @extend_schema(summary="Cash movements", responses={200: CashMovementSerializer(many=True)})
    def get(self, request: Request, pk) -> Response:
        shift = _get_shift(request, pk)
        movements = shift.cash_movements.select_related("user")
        return Response(CashMovementSerializer(movements, many=True).data)

    @extend_schema(
        summary="Record a cash movement",
        request=CashMovementRequestSerializer,
        responses={201: CashMovementSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        serializer = CashMovementRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        movement = services.record_cash_movement(
            shift=_get_shift(request, pk),
            movement_type=data["movement_type"],
            amount=data["amount"],
            reason=data["reason"],
            user=_acting_user(request),
        )
        return Response(CashMovementSerializer(movement).data, status=status.HTTP_201_CREATED)


class XReportView(APIView):
    """
    A mid-shift read that closes nothing.

    Honours `shifts.blind_close`: when on, the expected cash is withheld so the
    cashier's count stays an observation rather than a number worked backwards
    from a target.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = ""

    @extend_schema(summary="X-report", responses={200: ShiftReportSerializer})
    def get(self, request: Request, pk) -> Response:
        from apps.configuration import resolver
        from apps.configuration.resolver import ScopeContext

        shift = _get_shift(request, pk)
        report = services.x_report(shift)

        principal = auth_context(request)
        context = ScopeContext(
            organization_id=principal.organization_id, branch_id=principal.branch_id
        )
        if resolver.get("shifts.blind_close", context) and not principal.has("reports.financial"):
            report["expected_cash"] = None
            report["cash_sales"] = None

        return Response(report)


class CloseShiftView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman, HasPermission]
    required_permission = "shifts.close"

    @extend_schema(
        summary="Close a shift and freeze the Z-report",
        request=CloseShiftSerializer,
        responses={200: ShiftSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        serializer = CloseShiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        principal = auth_context(request)
        # A variance beyond the configured limit needs `shifts.close_with_variance`
        # or a step-up approval — see services.close_shift.
        approver = getattr(request, "approval_user", None)
        if approver is None and principal.has("shifts.close_with_variance"):
            approver = _acting_user(request)

        shift = services.close_shift(
            shift=_get_shift(request, pk),
            counted_cash=data["counted_cash"],
            reason=data.get("reason", ""),
            user=_acting_user(request),
            approved_by=approver,
        )
        return Response(ShiftSerializer(shift).data)


class ZReportView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = ""

    @extend_schema(summary="Z-report (frozen at close)", responses={200: None})
    def get(self, request: Request, pk) -> Response:
        shift = _get_shift(request, pk)
        if shift.status != ShiftStatus.CLOSED:
            raise AppError("الوردية لم تُغلق بعد", code="SHIFT_NOT_CLOSED")
        return Response(shift.z_report)
