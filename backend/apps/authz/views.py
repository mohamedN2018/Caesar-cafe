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

        try:
            validate_password(data["password"])
        except DjangoValidationError as exc:
            raise AppError(
                "كلمة المرور ضعيفة",
                code="WEAK_PASSWORD",
                errors={"password": list(exc.messages)},
            ) from exc

        with transaction.atomic():
            user = User.objects.create_user(
                email=data["email"],
                password=data["password"],
                organization_id=principal.organization_id,
                full_name_ar=data["full_name_ar"],
                phone=data.get("phone", ""),
            )
            RoleAssignment.objects.create(
                user=user,
                role=role,
                # Branch-scoped by default. An org-wide assignment is the
                # unusual case and has to be asked for, because getting it
                # backwards silently hands a branch manager every branch.
                branch_id=principal.branch_id if data["branch_scoped"] else None,
                created_by_id=principal.user_id,
            )

        audit.record(
            "staff.user_created",
            obj=user,
            object_label=user.full_name_ar or user.email,
            detail={"role": role.code, "branch_scoped": data["branch_scoped"]},
        )
        return Response(StaffSerializer(user).data, status=201)

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
