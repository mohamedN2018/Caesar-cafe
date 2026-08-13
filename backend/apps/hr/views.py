"""
The HR API. No rules here — `services.py` owns them.

Note which permission sits on which verb. Reading the rota needs `hr.view`;
writing it needs `hr.manage_roster`; recording a punch needs
`hr.record_attendance`; and correcting one needs `hr.amend_attendance`, which
nothing else implies. A shift leader who builds next week's rota does not thereby
get to rewrite last week's wages.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError, NotFoundError
from apps.core.viewsets import BranchScopedViewSet
from apps.organizations.models import Branch

from . import services
from .models import Attendance, WorkPattern, WorkShift
from .serializers import (
    AmendSerializer,
    AttendanceEventSerializer,
    AttendanceSerializer,
    PunchSerializer,
    TimesheetRowSerializer,
    WorkPatternSerializer,
    WorkShiftSerializer,
)


def _branch(request: Request) -> Branch:
    principal = auth_context(request)
    if principal.branch_id is None:
        raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED", status_code=400)
    return get_object_or_404(Branch, pk=principal.branch_id)


def _organization_id(request: Request):
    """
    The caller's organisation, narrowed to non-null.

    An authenticated principal always has one, but the type does not say so, and
    a `filter(organization_id=None)` would quietly match nothing rather than
    failing — which on the lookup below would turn a tenancy check into a 404 for
    everybody. Asserted once here instead of at four call sites.
    """
    organization_id = auth_context(request).organization_id
    if organization_id is None:
        raise AppError("لا يوجد سياق للمؤسسة.", code="ORGANIZATION_REQUIRED", status_code=400)
    return organization_id


def _actor(request: Request) -> User | None:
    user_id = auth_context(request).user_id
    if user_id is None:
        return None
    return User.objects.filter(pk=user_id).first()


def _staff_member(request: Request, user_id) -> User:
    """
    Resolve a person inside the caller's own organisation.

    Filtered by organisation rather than fetched by id alone: without it, a
    manager could punch in somebody from another tenant by passing their uuid,
    which is threat I1 in the plainest possible form.
    """
    user = User.objects.filter(pk=user_id, organization_id=_organization_id(request)).first()
    if user is None:
        raise NotFoundError("الموظف غير موجود.")
    return user


class WorkPatternViewSet(BranchScopedViewSet):
    """Named sets of hours — "صباحي ٨:٠٠–١٦:٠٠"."""

    queryset = WorkPattern.all_objects.all()
    serializer_class = WorkPatternSerializer
    required_permissions = {"GET": "hr.view", "default": "hr.manage_roster"}
    filterset_fields = ["is_active"]
    ordering_fields = ["starts_at", "name_ar"]


class WorkShiftViewSet(BranchScopedViewSet):
    """The rota: one person, one business day, one pattern."""

    queryset = WorkShift.objects.select_related("user", "pattern")
    serializer_class = WorkShiftSerializer
    required_permissions = {"GET": "hr.view", "default": "hr.manage_roster"}
    filterset_fields = ["user", "business_date", "pattern"]
    ordering_fields = ["business_date"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get("date_from"):
            queryset = queryset.filter(business_date__gte=params["date_from"])
        if params.get("date_to"):
            queryset = queryset.filter(business_date__lte=params["date_to"])
        return queryset


class AttendanceViewSet(BranchScopedViewSet):
    """
    The punches.

    `http_method_names` has no `post`, `put`, `patch` or `delete`. Attendance is
    created by a punch and corrected by an amendment, both of which are explicit
    actions with their own permission — a generic writable endpoint would let a
    row be edited with no reason recorded and nobody's name on it, which is the
    one thing this model exists to prevent.
    """

    queryset = Attendance.objects.select_related("user", "shift", "shift__pattern")
    serializer_class = AttendanceSerializer
    required_permissions = {"GET": "hr.view", "default": "hr.amend_attendance"}
    http_method_names = ["get", "head", "options"]
    filterset_fields = ["user", "business_date"]
    ordering_fields = ["business_date", "checked_in_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get("date_from"):
            queryset = queryset.filter(business_date__gte=params["date_from"])
        if params.get("date_to"):
            queryset = queryset.filter(business_date__lte=params["date_to"])
        if params.get("open") == "true":
            queryset = queryset.filter(checked_out_at__isnull=True, amended_out_at__isnull=True)
        return queryset

    def get_serializer_context(self):
        """
        Resolve the branch grace once per request, not once per row.

        A settings read per person is a query per person on the one screen that
        lists everybody.
        """
        context = super().get_serializer_context()
        principal = auth_context(self.request)
        if principal.branch_id is not None:
            branch = Branch.objects.filter(pk=principal.branch_id).first()
            if branch is not None:
                config = services.settings_for(branch)
                context["grace_minutes"] = int(config["hr.grace_minutes"])
        return context

    @extend_schema(summary="A person's punch history", responses={200: AttendanceEventSerializer})
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request: Request, pk=None) -> Response:
        """
        How this row got to its current values.

        Available to `hr.view` rather than `audit.view` on purpose: a shift leader
        settling an argument about Tuesday needs this one person's history, and
        granting them the organisation-wide audit trail to get it would be a much
        larger permission than the question requires.
        """
        attendance = self.get_object()
        return Response(AttendanceEventSerializer(attendance.events.all(), many=True).data)


class PunchView(APIView):
    """
    Recording arrivals and departures.

    Separate from the read-only viewset above because these are actions rather
    than writes to a row, and because they carry a different permission.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"default": "hr.record_attendance"}

    @extend_schema(
        summary="Record an arrival",
        request=PunchSerializer,
        responses={200: AttendanceSerializer},
    )
    def post(self, request: Request, kind: str) -> Response:
        if kind not in ("check-in", "check-out"):
            raise NotFoundError()

        serializer = PunchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        branch = _branch(request)
        user = _staff_member(request, data["user"])
        actor = _actor(request)

        from .models import PunchSource

        if kind == "check-in":
            attendance = services.check_in(
                user=user,
                branch=branch,
                at=data.get("at"),
                source=PunchSource.MANAGER,
                actor=actor,
                note=data.get("note", ""),
            )
        else:
            attendance = services.check_out(
                user=user, branch=branch, at=data.get("at"), actor=actor
            )

        return Response(AttendanceSerializer(attendance).data)


class AmendView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"default": "hr.amend_attendance"}

    @extend_schema(
        summary="Correct a punch, without erasing it",
        request=AmendSerializer,
        responses={200: AttendanceSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        attendance = Attendance.objects.filter(
            pk=pk, organization_id=_organization_id(request)
        ).first()
        if attendance is None:
            raise NotFoundError("سجل الحضور غير موجود.")

        serializer = AmendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        updated = services.amend(
            attendance=attendance,
            reason=data["reason"],
            actor=_actor(request),
            new_in=data.get("checked_in_at"),
            new_out=data.get("checked_out_at"),
        )
        return Response(AttendanceSerializer(updated).data)


class TimesheetView(APIView):
    """
    Hours per person over a range, computed on every read.

    `reports.employees` as well as `hr.view`: this is the screen a wage is
    calculated from, and it is the same class of information as the employee
    performance report that permission already guards.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"default": "hr.view"}

    @extend_schema(
        summary="Attendance totals per person",
        responses={200: TimesheetRowSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        branch = _branch(request)
        today = date.today()
        date_from = request.query_params.get("date_from") or str(today.replace(day=1))
        date_to = request.query_params.get("date_to") or str(today)

        try:
            parsed_from = date.fromisoformat(date_from)
            parsed_to = date.fromisoformat(date_to)
        except ValueError as exc:
            raise AppError("تاريخ غير صحيح.", code="INVALID_DATE") from exc

        # A cap, because the timesheet reads every punch in the range rather than
        # a rollup. 400 days matches the reports module's own limit.
        if parsed_to - parsed_from > timedelta(days=400):
            raise AppError("النطاق أطول من ٤٠٠ يوم.", code="RANGE_TOO_LONG")

        rows = services.timesheet(branch, date_from=parsed_from, date_to=parsed_to)
        return Response(TimesheetRowSerializer([row.__dict__ for row in rows], many=True).data)
