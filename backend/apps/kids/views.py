"""
Kids-area API.

The endpoints are thin: every rule that matters — capacity, age policy,
guardian verification, pricing — lives in `services`, because the Desktop calls
the same rules offline and a check enforced only in a view is a check the
offline path does not have.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.approval import consume_approval_token
from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import NotFoundError
from apps.core.play_pricing import compute_charge
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import Child, Guardian, PlayArea, PlayIncident, PlaySession, PlayTariff
from .serializers import (
    BoardSerializer,
    ChangeTariffSerializer,
    CheckInResponseSerializer,
    CheckInSerializer,
    CheckOutResponseSerializer,
    CheckOutSerializer,
    ChildSerializer,
    GuardianSerializer,
    IncidentCreateSerializer,
    OverrideChargeSerializer,
    PlayAreaSerializer,
    PlayIncidentSerializer,
    PlaySessionSerializer,
    PlayTariffSerializer,
    TariffPreviewSerializer,
)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _get_area(request: Request, area_id) -> PlayArea:
    principal = auth_context(request)
    area = PlayArea.objects.filter(id=area_id, branch_id=principal.branch_id).first()
    if area is None:
        raise NotFoundError("الصالة غير موجودة", code="AREA_NOT_FOUND")
    return area


def _get_session(request: Request, pk) -> PlaySession:
    principal = auth_context(request)
    session = (
        PlaySession.objects.filter(id=pk, branch_id=principal.branch_id)
        .select_related("area", "child", "guardian", "tariff")
        .first()
    )
    if session is None:
        raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")
    return session


# ── configuration ────────────────────────────────────────────────────────────


class PlayAreaViewSet(BranchScopedViewSet):
    queryset = PlayArea.all_objects.all()
    serializer_class = PlayAreaSerializer
    required_permissions = {"GET": "kids.view", "default": "kids.manage_areas"}
    pagination_class = None


class PlayTariffViewSet(BranchScopedViewSet):
    # Declared for the schema generator, which cannot introspect an override.
    queryset = PlayTariff.objects.all()
    serializer_class = PlayTariffSerializer
    required_permissions = {"GET": "kids.view", "default": "kids.manage_tariffs"}
    pagination_class = None

    def get_queryset(self):
        # A tariff hangs off an area rather than carrying its own branch, so
        # scoping walks the relation instead of inheriting the base filter.
        principal = auth_context(self.request)
        queryset = PlayTariff.objects.select_related("area")
        if principal.branch_id is not None:
            return queryset.filter(area__branch_id=principal.branch_id)
        return queryset.filter(area__organization_id=principal.organization_id)

    def perform_create(self, serializer) -> None:
        serializer.save(created_by_id=auth_context(self.request).user_id)


class GuardianViewSet(BranchScopedViewSet):
    queryset = Guardian.objects.all()
    serializer_class = GuardianSerializer
    required_permissions = {"GET": "kids.view", "default": "kids.checkin"}

    def get_queryset(self):
        queryset = super().get_queryset()
        if phone := self.request.query_params.get("phone"):
            # One tap turns a returning guardian's check-in into three fields.
            queryset = queryset.filter(phone__icontains=phone)
        return queryset


class ChildViewSet(BranchScopedViewSet):
    queryset = Child.objects.all()
    serializer_class = ChildSerializer
    required_permissions = {"GET": "kids.view", "default": "kids.checkin"}

    def get_queryset(self):
        principal = auth_context(self.request)
        queryset = Child.objects.select_related("guardian").filter(
            guardian__branch_id=principal.branch_id
        )
        if guardian := self.request.query_params.get("guardian"):
            queryset = queryset.filter(guardian_id=guardian)
        return queryset

    def perform_create(self, serializer) -> None:
        serializer.save(created_by_id=auth_context(self.request).user_id)


class PlayIncidentViewSet(BranchScopedViewSet):
    queryset = PlayIncident.objects.select_related("area", "session")
    serializer_class = PlayIncidentSerializer
    required_permissions = {"GET": "kids.view", "default": "kids.log_incident"}
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer) -> None:
        principal = auth_context(self.request)
        serializer.save(
            organization_id=principal.organization_id,
            branch_id=principal.branch_id,
            reported_by_id=principal.user_id,
            created_by_id=principal.user_id,
        )


# ── the floor ────────────────────────────────────────────────────────────────


class BoardView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.view"

    @extend_schema(summary="Live kids-area board", responses={200: BoardSerializer})
    def get(self, request: Request, area_id) -> Response:
        return Response(services.board(_get_area(request, area_id)))


class SessionListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.view"

    @extend_schema(summary="Play sessions", responses={200: PlaySessionSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        sessions = PlaySession.objects.filter(branch_id=principal.branch_id).select_related(
            "area", "child", "guardian"
        )

        if area := request.query_params.get("area"):
            sessions = sessions.filter(area_id=area)
        if status_filter := request.query_params.get("status"):
            sessions = sessions.filter(status=status_filter)
        if tag := request.query_params.get("tag"):
            sessions = sessions.filter(tag_number=tag)

        return Response([services.serialize_session(s) for s in sessions[:200]])


class CheckInView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.checkin"

    @extend_schema(
        summary="Check a child in",
        request=CheckInSerializer,
        responses={201: CheckInResponseSerializer},
    )
    def post(self, request: Request) -> Response:
        payload = CheckInSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        area = _get_area(request, data["area"])
        tariff = None
        if data.get("tariff"):
            tariff = PlayTariff.objects.filter(id=data["tariff"], area=area).first()
            if tariff is None:
                raise NotFoundError("التعريفة غير موجودة", code="TARIFF_NOT_FOUND")

        guardian = None
        if data.get("guardian_id"):
            guardian = Guardian.objects.filter(
                id=data["guardian_id"], branch_id=area.branch_id
            ).first()
            if guardian is None:
                raise NotFoundError("ولي الأمر غير موجود", code="GUARDIAN_NOT_FOUND")

        order = None
        if data.get("order"):
            from apps.orders.models import Order

            order = Order.objects.filter(id=data["order"], branch_id=area.branch_id).first()
            if order is None:
                raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")

        result = services.check_in(
            area=area,
            child_name=data["child_name"],
            guardian_name=data["guardian_name"],
            guardian_phone=data.get("guardian_phone", ""),
            guardian=guardian,
            age_months=data.get("age_months"),
            birth_date=data.get("birth_date"),
            tariff=tariff,
            tag_number=data["tag_number"],
            order=order,
            session_id=data.get("session_id"),
            medical_notes=data.get("medical_notes", ""),
            user=_acting_user(request),
        )
        return Response(
            {
                "session": services.serialize_session(result.session),
                "warnings": result.warnings,
            },
            status=201,
        )


class CheckOutView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.checkout"

    @extend_schema(
        summary="Check a child out and bill the session",
        request=CheckOutSerializer,
        responses={200: CheckOutResponseSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        payload = CheckOutSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        session = _get_session(request, pk)
        user = _acting_user(request)

        released_to = None
        if data.get("released_to_guardian"):
            released_to = Guardian.objects.filter(
                id=data["released_to_guardian"], branch_id=session.branch_id
            ).first()
            if released_to is None:
                raise NotFoundError("ولي الأمر غير موجود", code="GUARDIAN_NOT_FOUND")

        approval = None
        if token := data.get("approval_token"):
            granted = consume_approval_token(
                token, permission="kids.release_to_other", target=str(session.id)
            )
            if granted is not None:
                from apps.accounts.models import User

                approval = User.objects.filter(id=granted.approver_id).first()

        session = services.check_out(
            session,
            released_to=released_to,
            verified=data["verified"],
            approval=approval,
            user=user,
        )

        order = None
        if data["bill"]:
            target = None
            if data.get("bill_to_order"):
                from apps.orders.models import Order

                target = Order.objects.filter(
                    id=data["bill_to_order"], branch_id=session.branch_id
                ).first()
                if target is None:
                    raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")
            order = services.bill_session(session, order=target, user=user)
            session.refresh_from_db()

        return Response(
            {
                "session": services.serialize_session(session),
                "order_id": str(order.id) if order else None,
                "charge": str(session.payable),
            }
        )


class OverrideChargeView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.override_charge"

    @extend_schema(
        summary="Override a session charge",
        request=OverrideChargeSerializer,
        responses={200: PlaySessionSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        payload = OverrideChargeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        session = services.override_session_charge(
            _get_session(request, pk),
            amount=payload.validated_data["amount"],
            reason=payload.validated_data["reason"],
            user=_acting_user(request),
        )
        return Response(services.serialize_session(session))


class ChangeTariffView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.extend_session"

    @extend_schema(
        summary="Move a running session to another tariff",
        request=ChangeTariffSerializer,
        responses={200: PlaySessionSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        payload = ChangeTariffSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        session = _get_session(request, pk)
        tariff = PlayTariff.objects.filter(
            id=payload.validated_data["tariff"], area_id=session.area_id
        ).first()
        if tariff is None:
            raise NotFoundError("التعريفة غير موجودة", code="TARIFF_NOT_FOUND")

        session = services.change_tariff(session, tariff, user=_acting_user(request))
        return Response(services.serialize_session(session))


class IncidentView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.log_incident"

    @extend_schema(
        summary="Log a play-area incident",
        request=IncidentCreateSerializer,
        responses={201: PlayIncidentSerializer},
    )
    def post(self, request: Request) -> Response:
        payload = IncidentCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        area = _get_area(request, data["area"])
        session = None
        if data.get("session"):
            session = _get_session(request, data["session"])

        incident = services.log_incident(
            area=area,
            incident_type=data["incident_type"],
            description=data["description"],
            session=session,
            user=_acting_user(request),
            occurred_at=data.get("occurred_at"),
        )
        return Response(PlayIncidentSerializer(incident).data, status=201)


class TariffPreviewView(APIView):
    """
    Worked examples for the tariff builder.

    Runs the same `compute_charge` a real checkout runs, so what an admin sees
    while designing a rule is what a parent will be charged under it.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.view"

    #: Durations that expose the interesting boundaries: inside the included
    #: period, inside grace, one minute past it, and a long stay.
    DEFAULT_POINTS = (15, 30, 34, 38, 52, 90, 240)

    @extend_schema(
        summary="Preview what a tariff charges",
        responses={200: TariffPreviewSerializer(many=True)},
    )
    def get(self, request: Request, pk) -> Response:
        principal = auth_context(request)
        tariff = PlayTariff.objects.filter(id=pk, area__branch_id=principal.branch_id).first()
        if tariff is None:
            raise NotFoundError("التعريفة غير موجودة", code="TARIFF_NOT_FOUND")

        config = services.settings_for(tariff.area)
        pricing = services.to_pricing_tariff(tariff, grace=config["grace_minutes"])

        raw = request.query_params.get("minutes")
        points = [int(raw)] if raw else list(self.DEFAULT_POINTS)

        results = []
        for minutes in points:
            charge = compute_charge(pricing, minutes, rounding=config["rounding"])
            results.append(
                {
                    "minutes": minutes,
                    "charge": str(charge.charge),
                    "billable_minutes": charge.billable_minutes,
                    "blocks": charge.blocks,
                    "capped": charge.capped,
                }
            )
        return Response(results)


class KidsReportView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kids.view_reports"

    @extend_schema(summary="Kids-area revenue and occupancy", responses={200: None})
    def get(self, request: Request) -> Response:
        from apps.organizations.models import Branch

        principal = auth_context(request)
        branch = Branch.objects.filter(id=principal.branch_id).first()
        if branch is None:
            raise NotFoundError("الفرع غير موجود", code="BRANCH_NOT_FOUND")

        days = int(request.query_params.get("days", 30))
        since = timezone.now() - timedelta(days=days)

        return Response({"days": days, **services.report(branch, since=since)})
