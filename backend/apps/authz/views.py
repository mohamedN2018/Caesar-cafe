"""
Staff and roles.

The screen an owner needs before anything else works: without it there is no way
to create the cashier who will stand at the till, and no way to give them the PIN
the Desktop authenticates against.

Three rules shape the endpoints:

  * **Nothing here ever returns a secret.** Not `pin_hash`, not `password`. A
    staff list that could read either would turn one compromised manager session
    into every terminal in the branch.
  * **A user is deactivated, never deleted.** Their name is on last quarter's
    voids and shift closures, and deleting them would rewrite that history into
    "unknown".
  * **A role is edited, never deleted, if it is a system role.** An owner who
    does not want cashiers voiding items should be able to say so; deleting the
    CASHIER role instead would orphan every assignment pointing at it.

`staff.reset_pin` is separate from `staff.manage_users` because they answer to
different risks. Editing a phone number is administration; setting the secret
that unlocks a till is not.
"""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.audit import services as audit
from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError, NotFoundError, PermissionDeniedError

from . import catalog, services
from .drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from .models import Role, RoleAssignment
from .serializers import (
    PermissionDefSerializer,
    ResetPinSerializer,
    RoleAssignmentSerializer,
    RoleSerializer,
    SetActiveSerializer,
    StaffCreateSerializer,
    StaffSerializer,
)


def _acting_user(request: Request) -> User | None:
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    return User.objects.filter(id=principal.user_id).first()


class PermissionCatalogView(APIView):
    """
    The shipped permission catalogue, grouped.

    Served rather than duplicated in the SPA: a role editor whose list of
    permissions was written by hand in TypeScript would drift from the server's
    the first time a code was added, and the drift would show up as a permission
    nobody can grant.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "staff.view"

    @extend_schema(
        summary="Every permission code the product defines",
        responses={200: PermissionDefSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        return Response(
            PermissionDefSerializer(
                [
                    {
                        "code": p.code,
                        "group": p.group,
                        "label_ar": p.label_ar,
                        "description_ar": p.description_ar,
                        "sensitive": p.sensitive,
                    }
                    for p in catalog.PERMISSIONS
                ],
                many=True,
            ).data
        )


class RoleViewSet(viewsets.ModelViewSet):
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "staff.view", "default": "staff.manage_roles"}
    pagination_class = None

    def get_queryset(self):
        return (
            Role.objects.filter(organization_id=auth_context(self.request).organization_id)
            .annotate(assignment_count=Count("assignments", distinct=True))
            .prefetch_related("permissions")
            .order_by("code")
        )

    def perform_create(self, serializer) -> None:
        role = serializer.save(
            organization_id=auth_context(self.request).organization_id,
            created_by_id=auth_context(self.request).user_id,
        )
        audit.record(
            "staff.role_changed", obj=role, object_label=role.name_ar, after={"created": True}
        )

    def perform_update(self, serializer) -> None:
        before = sorted(serializer.instance.permission_codes)
        role = serializer.save(updated_by_id=auth_context(self.request).user_id)

        # Cached permission sets are read on the hot path of every request. A
        # role edit that did not invalidate them would take effect whenever the
        # cache happened to expire, which is indistinguishable from a bug.
        services.invalidate_all()
        audit.record(
            "staff.role_changed",
            obj=role,
            object_label=role.name_ar,
            before={"permissions": before},
            after={"permissions": sorted(role.permission_codes)},
        )

    def perform_destroy(self, instance: Role) -> None:
        if instance.is_system:
            raise AppError(
                f"الدور '{instance.name_ar}' دور نظام ولا يمكن حذفه. يمكن تعديل صلاحياته.",
                code="SYSTEM_ROLE_NOT_DELETABLE",
                status_code=409,
            )
        if instance.assignments.exists():
            raise AppError(
                "لا يمكن حذف دور مسنَد لمستخدمين — انقلهم إلى دور آخر أولاً.",
                code="ROLE_IN_USE",
                status_code=409,
            )
        instance.delete()
        services.invalidate_all()


class StaffViewSet(viewsets.ModelViewSet):
    """
    People, not accounts. Read and edit; creation goes through `POST /staff/`
    with its own serializer because it also mints a role assignment.
    """

    serializer_class = StaffSerializer
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "staff.view", "default": "staff.manage_users"}
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        principal = auth_context(self.request)
        return (
            User.objects.filter(organization_id=principal.organization_id)
            .select_related("staff_profile")
            .prefetch_related("role_assignments__role", "role_assignments__branch")
            .order_by("full_name_ar")
        )

    @extend_schema(
        summary="Create a staff member and assign their role",
        request=StaffCreateSerializer,
        responses={201: StaffSerializer},
    )
    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        principal = auth_context(request)

        if User.objects.filter(email__iexact=data["email"]).exists():
            raise AppError(
                "هذا البريد مستخدم بالفعل",
                code="EMAIL_TAKEN",
                errors={"email": ["البريد مستخدم بالفعل"]},
            )

        role = Role.objects.filter(
            organization_id=principal.require_organization(), code=data["role"]
        ).first()
        if role is None:
            raise NotFoundError("الدور غير موجود", code="ROLE_NOT_FOUND")

        password = data.get("password")
        if password:
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                raise AppError(
                    "كلمة المرور ضعيفة",
                    code="WEAK_PASSWORD",
                    errors={"password": list(exc.messages)},
                ) from exc

        pin_length = resolver.get(
            "security.pin_length", ScopeContext(organization_id=principal.organization_id)
        )
        pin = _next_free_pin(principal.organization_id, pin_length)

        with transaction.atomic():
            user = User.objects.create_user(
                email=data["email"],
                password=password,
                organization_id=principal.organization_id,
                full_name_ar=data["full_name_ar"],
                phone=data.get("phone", ""),
            )
            if not password:
                # Unusable, not blank and not a default. Email sign-in is closed
                # for this person until somebody deliberately opens it.
                user.set_unusable_password()

            # **Every staff member gets a PIN, always.** The till is the normal
            # way in, and a person created without one is a person who cannot
            # work until a second, easily-forgotten step is done — which in
            # practice means a manager lends them theirs, and the audit trail
            # starts naming the wrong human.
            user.set_pin(pin)
            user.save(update_fields=["password", "pin_hash", "pin_set_at"])

            RoleAssignment.objects.create(
                user=user,
                role=role,
                # Branch-scoped by default. An org-wide assignment is the
                # unusual case and has to be asked for, because getting it
                # backwards silently hands a branch manager every branch.
                branch_id=principal.branch_id if data["branch_scoped"] else None,
                created_by_id=principal.user_id,
            )
            badge_token = _issue_badge(user, issued_by=_acting_user(request))

        audit.record(
            "staff.user_created",
            obj=user,
            object_label=user.full_name_ar or user.email,
            detail={
                "role": role.code,
                "branch_scoped": data["branch_scoped"],
                "can_sign_in_with_email": bool(password),
            },
        )

        # The PIN and the badge are returned ONCE, here, because this response
        # is what prints the card. Neither is readable afterwards — the staff
        # list can say a person HAS a badge, never what it says.
        return Response(
            {
                **StaffSerializer(user).data,
                "credentials": {
                    "pin": pin,
                    "badge": badge_token,
                    "name": user.full_name_ar,
                    "note": "اطبع البطاقة الآن — لن يظهر الرمز مرة أخرى.",
                },
            },
            status=201,
        )

    def perform_update(self, serializer) -> None:
        before = {
            "is_active": serializer.instance.is_active,
            "full_name_ar": serializer.instance.full_name_ar,
        }
        user = serializer.save(updated_by_id=auth_context(self.request).user_id)

        if before["is_active"] and not user.is_active:
            audit.record(
                "staff.user_deactivated", obj=user, object_label=user.full_name_ar, before=before
            )

    @extend_schema(
        summary="Set a staff member's POS PIN administratively",
        request=ResetPinSerializer,
        responses={200: StaffSerializer},
    )
    @action(detail=True, methods=["post"], url_path="reset-pin")
    def reset_pin(self, request: Request, pk=None) -> Response:
        principal = auth_context(request)
        if not principal.has("staff.reset_pin"):
            raise PermissionDeniedError("يتطلب صلاحية: staff.reset_pin")

        serializer = ResetPinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = self.get_object()
        required_length = resolver.get(
            "security.pin_length", ScopeContext(organization_id=user.organization_id)
        )
        pin = serializer.validated_data["pin"]
        if len(pin) != required_length:
            raise AppError(
                f"رمز الدخول يجب أن يكون {required_length} أرقام",
                code="PIN_LENGTH_INVALID",
                errors={"pin": [f"مطلوب {required_length} أرقام"]},
            )

        user.set_pin(pin)
        user.save(update_fields=["pin_hash", "pin_set_at"])

        # The PIN itself is never in the record — only that it changed, who
        # changed it, and for whom. That is what a dispute needs; the value is
        # not, and storing it would defeat hashing it.
        audit.record(
            "staff.pin_reset",
            obj=user,
            object_label=user.full_name_ar or user.email,
            actor=_acting_user(request),
            detail={"administrative": True},
        )
        return Response(StaffSerializer(user).data)

    @extend_schema(
        summary="Assign a role", request=RoleAssignmentSerializer, responses={200: StaffSerializer}
    )
    @action(detail=True, methods=["post"], url_path="assign-role")
    def assign_role(self, request: Request, pk=None) -> Response:
        principal = auth_context(request)
        if not principal.has("staff.manage_roles"):
            raise PermissionDeniedError("يتطلب صلاحية: staff.manage_roles")

        user = self.get_object()
        role = Role.objects.filter(
            id=request.data.get("role"), organization_id=principal.require_organization()
        ).first()
        if role is None:
            raise NotFoundError("الدور غير موجود", code="ROLE_NOT_FOUND")

        branch_id = request.data.get("branch") or None
        RoleAssignment.objects.get_or_create(
            user=user,
            role=role,
            branch_id=branch_id,
            defaults={"created_by_id": principal.require_user()},
        )
        services.invalidate_user(user.id)

        audit.record(
            "staff.role_changed",
            obj=user,
            object_label=user.full_name_ar or user.email,
            after={"granted": role.code},
        )
        user.refresh_from_db()
        return Response(StaffSerializer(user).data)

    @extend_schema(
        summary="Print a new badge for this person", responses={200: OpenApiTypes.OBJECT}
    )
    @action(detail=True, methods=["post"], url_path="badge")
    def reissue_badge(self, request: Request, pk=None) -> Response:
        """
        Gated on `staff.reset_pin`, not `staff.manage_users`.

        A badge unlocks a till exactly as a PIN does, so it answers to the same
        risk. Editing somebody's phone number is administration; minting the
        thing that lets a person ring up sales as them is not.
        """
        principal = auth_context(request)
        if not principal.has("staff.reset_pin"):
            raise PermissionDeniedError("يتطلب صلاحية: staff.reset_pin")

        user = self.get_object()
        token = _issue_badge(user, issued_by=_acting_user(request))

        audit.record(
            "staff.badge_issued",
            obj=user,
            object_label=user.full_name_ar or user.email,
            actor=_acting_user(request),
            detail={"reissued": True},
        )
        # Once, like the PIN. The old card stopped working the moment this
        # returned, which is the entire point of reprinting one.
        return Response({"badge": token, "name": user.full_name_ar})

    @extend_schema(
        summary="What this person has been doing",
        parameters=[
            OpenApiParameter("days", OpenApiTypes.INT, description="Window, default 30."),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["get"], url_path="activity")
    def activity(self, request: Request, pk=None) -> Response:
        """
        Orders rung, money taken, and every sensitive thing they did.

        An owner asking "what has this person been doing" wants one number they
        can compare across staff and one list they can read. Both come from
        records that already exist — orders, payments, the audit trail — rather
        than from a second tally kept alongside, which could disagree with them.
        """
        from datetime import timedelta

        from apps.audit.models import AuditLog
        from apps.orders.models import Order, OrderEvent
        from apps.payments.models import Payment

        user = self.get_object()
        days = max(1, min(int(request.query_params.get("days", 30)), 365))
        since = timezone.now() - timedelta(days=days)

        events = OrderEvent.objects.filter(actor=user, recorded_at__gte=since)
        trail = AuditLog.objects.filter(actor=user, occurred_at__gte=since)

        return Response(
            {
                "user": {"id": str(user.id), "full_name_ar": user.full_name_ar},
                "days": days,
                "orders_opened": Order.objects.filter(opened_by=user, opened_at__gte=since).count(),
                # Every event they appended MINUS the one that opens an order,
                # which would otherwise count each order twice — once as an
                # order and again as a change to it.
                "changes_made": events.exclude(event_type="ORDER_OPENED").count(),
                "payments_taken": Payment.objects.filter(
                    received_by=user, paid_at__gte=since
                ).count(),
                # The three an owner actually watches, broken out rather than
                # buried in a total: they are the ones that move money without
                # selling anything.
                "items_voided": events.filter(event_type="ITEM_VOIDED").count(),
                "discounts_given": events.filter(event_type="DISCOUNT_APPLIED").count(),
                "prices_overridden": events.filter(event_type="ITEM_PRICE_OVERRIDDEN").count(),
                "approvals_given": trail.filter(action="auth.step_up_approved").count(),
                "recent": [
                    {
                        "action": row.action,
                        "label": row.object_label,
                        "at": row.occurred_at,
                        "severity": row.severity,
                    }
                    for row in trail.order_by("-occurred_at")[:50]
                ],
            }
        )

    @extend_schema(
        summary="Remove a role assignment",
        request=None,
        responses={200: StaffSerializer},
        # `assignment_id` is not a field on User, so spectacular cannot infer it
        # from the queryset the way it infers `id`.
        parameters=[
            OpenApiParameter(
                "assignment_id",
                OpenApiTypes.UUID,
                OpenApiParameter.PATH,
                description="The RoleAssignment to remove.",
            )
        ],
    )
    @action(detail=True, methods=["post"], url_path="revoke-role/(?P<assignment_id>[^/.]+)")
    def revoke_role(self, request: Request, pk=None, assignment_id=None) -> Response:
        principal = auth_context(request)
        if not principal.has("staff.manage_roles"):
            raise PermissionDeniedError("يتطلب صلاحية: staff.manage_roles")

        user = self.get_object()
        assignment = user.role_assignments.filter(id=assignment_id).select_related("role").first()
        if assignment is None:
            raise NotFoundError("الإسناد غير موجود", code="ASSIGNMENT_NOT_FOUND")

        # Removing somebody's last role leaves an account that can log in and do
        # nothing — a support call that looks like a broken system rather than a
        # configuration mistake. Deactivate the user instead.
        if user.role_assignments.count() == 1:
            raise AppError(
                "لا يمكن إزالة الدور الأخير — أوقف المستخدم بدلاً من ذلك.",
                code="LAST_ROLE",
                status_code=409,
            )

        code = assignment.role.code
        assignment.delete()
        services.invalidate_user(user.id)

        audit.record(
            "staff.role_changed",
            obj=user,
            object_label=user.full_name_ar or user.email,
            after={"revoked": code},
        )
        user.refresh_from_db()
        return Response(StaffSerializer(user).data)

    @extend_schema(
        summary="Activate or deactivate a staff member",
        request=SetActiveSerializer,
        responses={200: StaffSerializer},
    )
    @action(detail=True, methods=["post"], url_path="set-active")
    def set_active(self, request: Request, pk=None) -> Response:
        serializer = SetActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = self.get_object()
        principal = auth_context(request)

        # Locking yourself out of your own organization is a support call that
        # cannot be resolved from inside the product.
        if user.id == principal.user_id and not serializer.validated_data["is_active"]:
            raise AppError("لا يمكنك إيقاف حسابك.", code="CANNOT_DEACTIVATE_SELF", status_code=409)

        user.is_active = serializer.validated_data["is_active"]
        user.save(update_fields=["is_active", "updated_at"])

        if not user.is_active:
            audit.record(
                "staff.user_deactivated", obj=user, object_label=user.full_name_ar or user.email
            )
        return Response(StaffSerializer(user).data)


# ── badges and activity, appended to StaffViewSet below ──────────────────────


def _next_free_pin(organization_id, length: int) -> str:
    """
    A PIN nobody in the organization is already using.

    Uniqueness is not cosmetic here: `pos-login` identifies a person BY their
    PIN, with no username to disambiguate. Two cashiers sharing 1234 would mean
    whichever row the database returned first gets the sale — and the audit
    trail would name the wrong human, silently, forever.

    Random rather than sequential, because 1001, 1002, 1003 down the staff list
    is a pattern anybody standing at the till can finish guessing.
    """
    import secrets

    from apps.accounts.models import User as StaffUser

    taken_hashes = StaffUser.objects.filter(organization_id=organization_id).exclude(pin_hash="")
    span = 10**length

    for _ in range(200):
        candidate = str(secrets.randbelow(span)).zfill(length)
        # Hashes are salted, so "is it taken" is a check against each, not a
        # lookup. The staff list of a cafe is tens of rows, not thousands.
        if not any(u.check_pin(candidate) for u in taken_hashes):
            return candidate

    raise AppError(
        "تعذّر توليد رمز دخول غير مستخدم — وسّع طول الرمز من الإعدادات",
        code="PIN_SPACE_EXHAUSTED",
    )


def _issue_badge(user, *, issued_by=None) -> str:
    """
    Mint a badge, revoking whatever the person held before.

    One live badge per person, deliberately. Reprinting is what somebody does
    when a card is lost or left on a counter, and a system that let both keep
    working would make reprinting useless for the one case it exists for.
    """
    from apps.accounts.badges import Badge, fingerprint, mint

    Badge.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())

    raw = mint()
    Badge.objects.create(
        user=user,
        token_hash=fingerprint(raw),
        # The name is ON the card. A drawer of identical QR codes is a drawer
        # nobody can sort, and the first thing a manager needs from a stack of
        # badges is whose is whose.
        label=user.full_name_ar or user.email,
        issued_by=issued_by,
    )
    return raw
