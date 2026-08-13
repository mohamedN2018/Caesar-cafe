"""
Generic settings API.

These endpoints read the registry rather than enumerating keys, so adding a
setting requires no API change and no new endpoint — the property that makes
C10 ("everything is configurable") survivable for the developers.
"""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError, NotFoundError, PermissionDeniedError

from . import resolver
from .models import SettingChangeLog
from .registry import Scope, ValidationFailed, registry
from .serializers import (
    SettingHistoryResponseSerializer,
    SettingListResponseSerializer,
    SettingSchemaResponseSerializer,
    SettingWriteRequestSerializer,
    SettingWriteResponseSerializer,
)


def _client_ip(request: Request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _acting_user(request: Request):
    """
    The User row behind the token.

    `request.user` is Django's session user and is anonymous here — the principal
    comes from AuthContextMiddleware, not from a session cookie.
    """
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _scope_context(request: Request) -> resolver.ScopeContext:
    """
    Build the read scope from the authenticated principal.

    Organization is ALWAYS taken from the token, never from a query parameter —
    otherwise any authenticated user could read another tenant's configuration
    by changing a URL (threat I1). Branch and device may be narrowed via query
    params, but only within the caller's own organization.
    """
    principal = auth_context(request)

    def as_uuid(name: str) -> UUID | None:
        raw = request.query_params.get(name)
        if not raw:
            return None
        try:
            return UUID(raw)
        except ValueError as exc:
            raise AppError(f"معرّف غير صالح: {name}", code="INVALID_SCOPE_ID") from exc

    branch_id = as_uuid("branch") or principal.branch_id
    if branch_id is not None:
        from apps.organizations.models import Branch

        owns_branch = Branch.objects.filter(
            id=branch_id, organization_id=principal.require_organization()
        ).exists()
        if not owns_branch:
            raise NotFoundError("الفرع غير موجود", code="BRANCH_NOT_FOUND")

    return resolver.ScopeContext(
        organization_id=principal.organization_id,
        branch_id=branch_id,
        device_id=as_uuid("device") or principal.device_id,
        role_id=as_uuid("role"),
    )


def _assert_scope_belongs_to_principal(request: Request, scope: Scope, scope_id: UUID) -> None:
    """A write must land inside the caller's own tenant."""
    principal = auth_context(request)
    org_id = principal.organization_id

    if scope is Scope.ORGANIZATION:
        owned = scope_id == org_id
    elif scope is Scope.BRANCH:
        from apps.organizations.models import Branch

        owned = Branch.objects.filter(id=scope_id, organization_id=org_id).exists()
    elif scope is Scope.ROLE:
        from apps.authz.models import Role

        owned = Role.objects.filter(id=scope_id, organization_id=org_id).exists()
    else:  # DEVICE — validated against the licence in Phase 3
        owned = True

    if not owned:
        raise NotFoundError("النطاق غير موجود", code="SCOPE_NOT_FOUND")


class SettingSchemaView(APIView):
    """
    The registry, as data.

    The Web Admin renders its entire settings UI from this — grouped, typed,
    validated, with Arabic labels and help. A new setting appears in the UI
    with no frontend change.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    # Metadata only — labels, types, defaults. Anyone who may view branch
    # configuration needs it to render the settings screen. What a caller may
    # actually *change* is gated per key by `definition.permission` on write.
    required_permission = "branch.view"

    @extend_schema(
        summary="Settings registry schema",
        responses={200: SettingSchemaResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        groups: dict[str, list[dict]] = {}
        for definition in registry.all().values():
            groups.setdefault(definition.group, []).append(
                {
                    "key": definition.key,
                    "type": definition.type.value,
                    "scope": definition.scope.value,
                    "default": definition.serialize(definition.default),
                    "label_ar": definition.label_ar,
                    "label_en": definition.label_en,
                    "help_ar": definition.help_ar,
                    "choices": list(definition.choices),
                    "permission": definition.permission,
                    "high_impact": definition.high_impact,
                    "affects_open_orders": definition.affects_open_orders,
                    "pushes_to_desktop": definition.pushes_to_desktop,
                }
            )
        return Response({"groups": groups, "count": len(registry.all())})


class SettingListView(APIView):
    """Resolved values for a scope, each tagged with where it came from."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "branch.view", "PATCH": "branch.edit_settings"}

    @extend_schema(
        summary="Resolved settings for a scope",
        responses={200: SettingListResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        context = _scope_context(request)
        group_filter = request.query_params.get("group")

        resolved = resolver.resolve_all(context)
        payload = {}
        for key, item in resolved.items():
            definition = registry.get(key)
            if group_filter and definition.group != group_filter:
                continue
            payload[key] = {
                "value": definition.serialize(item.value),
                "origin": item.origin,
                "is_default": item.is_default,
            }
        return Response({"settings": payload})

    @extend_schema(
        summary="Write setting overrides",
        request=SettingWriteRequestSerializer,
        responses={
            200: SettingWriteResponseSerializer,
            207: SettingWriteResponseSerializer,
        },
    )
    def patch(self, request: Request) -> Response:
        scope_raw = request.data.get("scope")
        scope_id_raw = request.data.get("scope_id")
        values = request.data.get("values") or {}

        if not scope_raw or not scope_id_raw:
            raise AppError(
                "يجب تحديد النطاق ومعرّفه",
                code="SCOPE_REQUIRED",
                errors={"scope": ["مطلوب"], "scope_id": ["مطلوب"]},
            )
        if not isinstance(values, dict) or not values:
            raise AppError("لا توجد قيم للحفظ", code="NO_VALUES")

        try:
            scope = Scope(scope_raw)
            scope_id = UUID(str(scope_id_raw))
        except ValueError as exc:
            raise AppError("نطاق غير صالح", code="INVALID_SCOPE") from exc

        _assert_scope_belongs_to_principal(request, scope, scope_id)

        principal = auth_context(request)
        actor = _acting_user(request)
        applied: dict[str, object] = {}
        errors: dict[str, list[str]] = {}

        # Each key is validated and written independently: one bad value must
        # not discard the operator's other changes.
        for key, raw_value in values.items():
            if key not in registry:
                errors[key] = ["إعداد غير معروف"]
                continue

            # Per-setting permission. `branch.edit_settings` is the entry ticket;
            # security and licensing keys need their own, so a branch manager
            # cannot loosen security.require_mfa_for_roles.
            definition = registry.get(key)
            if not principal.has(definition.permission):
                errors[key] = [f"يتطلب صلاحية: {definition.permission}"]
                continue

            try:
                result = resolver.set_value(
                    key,
                    raw_value,
                    scope=scope,
                    scope_id=scope_id,
                    user=actor,
                    ip_address=_client_ip(request),
                )
                applied[key] = registry.get(key).serialize(result.value)
            except (ValidationFailed, ValueError) as exc:
                errors[key] = [str(exc)]

        if errors and not applied:
            raise AppError(
                "تعذّر حفظ الإعدادات",
                code="SETTINGS_VALIDATION_FAILED",
                errors=errors,
            )

        return Response(
            {"applied": applied, "errors": errors},
            status=status.HTTP_200_OK if not errors else status.HTTP_207_MULTI_STATUS,
        )


class SettingDetailView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "branch.edit_settings"

    @extend_schema(
        summary="Reset a setting to its inherited or default value",
        responses={204: None},
    )
    def delete(self, request: Request, key: str) -> Response:
        if key not in registry:
            raise NotFoundError("إعداد غير معروف", code="SETTING_NOT_FOUND")

        try:
            scope = Scope(request.query_params.get("scope", ""))
            scope_id = UUID(request.query_params.get("scope_id", ""))
        except ValueError as exc:
            raise AppError("نطاق غير صالح", code="INVALID_SCOPE") from exc

        _assert_scope_belongs_to_principal(request, scope, scope_id)

        definition = registry.get(key)
        if not auth_context(request).has(definition.permission):
            raise PermissionDeniedError(f"يتطلب صلاحية: {definition.permission}")

        resolver.reset(
            key,
            scope=scope,
            scope_id=scope_id,
            user=_acting_user(request),
            ip_address=_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SettingHistoryView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "audit.view"

    @extend_schema(
        summary="Who changed what, and when",
        responses={200: SettingHistoryResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        queryset = SettingChangeLog.objects.all()
        if key := request.query_params.get("key"):
            queryset = queryset.filter(key=key)
        if scope_id := request.query_params.get("scope_id"):
            queryset = queryset.filter(scope_id=scope_id)

        entries = [
            {
                "key": row.key,
                "scope_type": row.scope_type,
                "scope_id": str(row.scope_id),
                "old_value": row.old_value,
                "new_value": row.new_value,
                "changed_by": row.changed_by.get_username() if row.changed_by else None,
                "ip_address": row.ip_address,
                "created_at": row.created_at.isoformat(),
            }
            for row in queryset.select_related("changed_by")[:200]
        ]
        return Response({"history": entries})
