"""Authentication endpoints."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.authz.approval import issue_approval_token
from apps.authz.catalog import is_valid as is_valid_permission
from apps.authz.context import PrincipalKind
from apps.authz.drf import (
    AllowsEnrollment,
    IsAuthenticatedPrincipal,
    PublicEndpointMixin,
    auth_context,
)
from apps.authz.models import RoleAssignment
from apps.authz.services import effective_permissions
from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError, PermissionDeniedError

from . import services, tokens, totp
from .models import User
from .serializers import (
    ApprovalTokenSerializer,
    ChangePasswordSerializer,
    LoginRequestSerializer,
    LogoutRequestSerializer,
    MeSerializer,
    MFAConfirmSerializer,
    MFASetupSerializer,
    RefreshRequestSerializer,
    SetPinSerializer,
    SimpleResultSerializer,
    TokenPairSerializer,
    VerifyPinRequestSerializer,
)

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _validated(serializer_class, request: Request) -> dict:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class LoginView(PublicEndpointMixin, APIView):
    """Web login. Rate-limited per IP and per account (threat D1)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(
        summary="Log in with email and password",
        request=LoginRequestSerializer,
        responses={200: TokenPairSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(LoginRequestSerializer, request)
        ip = _client_ip(request)
        user_agent = request.headers.get("User-Agent", "")

        user = services.authenticate_password(
            email=data["email"],
            password=data["password"],
            ip_address=ip,
            user_agent=user_agent,
        )

        if services.mfa_is_required(user) or user.mfa_enabled:
            self._check_mfa(user, data, ip)

        user.last_login = timezone.now()
        user.last_login_ip = ip
        user.save(update_fields=["last_login", "last_login_ip"])

        pair = tokens.issue_pair(
            user=user,
            kind=PrincipalKind.WEB,
            organization_id=user.organization_id,
            branch_id=self._default_branch_id(user),
            ip_address=ip,
            user_agent=user_agent,
        )
        pair.pop("family_id", None)
        return Response(pair)

    @staticmethod
    def _default_branch_id(user: User):
        """
        Pre-select the branch when it is unambiguous.

        Single-branch cafes are the norm, and making the client fetch a branch
        list before it can do anything is friction for no benefit. With several
        branches this stays None and the user picks one — their permissions are
        the union across branches until they do.
        """
        branch_ids = set(
            RoleAssignment.objects.filter(user=user, branch__isnull=False).values_list(
                "branch_id", flat=True
            )
        )
        if branch_ids:
            return branch_ids.pop() if len(branch_ids) == 1 else None

        from apps.organizations.models import Branch

        owned = list(
            Branch.objects.filter(organization_id=user.organization_id, is_active=True).values_list(
                "id", flat=True
            )[:2]
        )
        return owned[0] if len(owned) == 1 else None

    @staticmethod
    def _check_mfa(user: User, data: dict, ip: str | None) -> None:
        if not user.mfa_enabled:
            # Policy requires MFA but this account has not enrolled. Refuse the
            # session — but hand back a scoped enrolment token, otherwise the
            # user is locked out: login needs MFA, and enrolling needs a token.
            enrollment = tokens.issue_enrollment_token(
                user=user, organization_id=user.organization_id
            )
            raise AppError(
                "هذا الحساب يتطلب تفعيل التحقق بخطوتين قبل الدخول.",
                code="MFA_ENROLLMENT_REQUIRED",
                extra={"enroll_url": "/api/v1/auth/mfa/setup/", **enrollment},
            )

        if recovery := data.get("recovery_code"):
            if services.consume_recovery_code(user, recovery):
                return
            services.log_attempt(identifier=user.email, kind="MFA", succeeded=False, ip_address=ip)
            raise services.AuthenticationFailed("رمز الاسترداد غير صحيح", code="MFA_INVALID")

        code = (data.get("mfa_code") or "").strip()
        if not code:
            raise services.MFARequired()

        if not totp.verify(user.mfa_secret, code):
            services.log_attempt(identifier=user.email, kind="MFA", succeeded=False, ip_address=ip)
            raise services.AuthenticationFailed("رمز التحقق غير صحيح", code="MFA_INVALID")


class RefreshView(PublicEndpointMixin, APIView):
    """Rotate a refresh token. Reuse revokes every session for the account."""

    @extend_schema(
        summary="Rotate the refresh token",
        request=RefreshRequestSerializer,
        responses={200: TokenPairSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(RefreshRequestSerializer, request)
        return Response(tokens.rotate(data["refresh"], ip_address=_client_ip(request)))


class LogoutView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Log out",
        request=LogoutRequestSerializer,
        responses={200: SimpleResultSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(LogoutRequestSerializer, request)
        tokens.revoke(data["refresh"])
        return Response({"detail": "تم تسجيل الخروج"})


class MeView(APIView):
    """Profile plus the effective permission set the UI uses to shape itself."""

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(summary="Current principal", responses={200: MeSerializer})
    def get(self, request: Request) -> Response:
        context = auth_context(request)
        user = User.objects.get(id=context.user_id)

        roles = list(RoleAssignment.objects.filter(user=user).values_list("role__code", flat=True))

        return Response(
            {
                "id": user.id,
                "email": user.email,
                "full_name_ar": user.full_name_ar,
                "full_name_en": user.full_name_en,
                "organization_id": user.organization_id,
                "branch_id": context.branch_id,
                "kind": context.kind.value,
                "is_superuser": user.is_superuser,
                "mfa_enabled": user.mfa_enabled,
                "has_pin": user.has_pin,
                "permissions": sorted(context.permissions),
                "roles": roles,
            }
        )


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Change your own password",
        request=ChangePasswordSerializer,
        responses={200: SimpleResultSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(ChangePasswordSerializer, request)
        user = User.objects.get(id=auth_context(request).user_id)

        if not user.check_password(data["current_password"]):
            raise services.AuthenticationFailed("كلمة المرور الحالية غير صحيحة")

        user.set_password(data["new_password"])
        user.save(update_fields=["password"])

        # A password change must end every other session — that is the point of
        # changing it after a suspected compromise.
        from .models import TokenFamily

        TokenFamily.revoke_all_for_user(user.id, reason="PASSWORD_CHANGED")
        return Response({"detail": "تم تغيير كلمة المرور. برجاء تسجيل الدخول مرة أخرى."})


class SetPinView(APIView):
    """Setting a POS PIN requires the account password, not just a session."""

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Set your POS PIN",
        request=SetPinSerializer,
        responses={200: SimpleResultSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(SetPinSerializer, request)
        user = User.objects.get(id=auth_context(request).user_id)

        if not user.check_password(data["current_password"]):
            raise services.AuthenticationFailed("كلمة المرور غير صحيحة")

        context = ScopeContext(organization_id=user.organization_id)
        required_length = resolver.get("security.pin_length", context)
        if len(data["pin"]) != required_length:
            raise AppError(
                f"رمز الدخول يجب أن يكون {required_length} أرقام",
                code="PIN_LENGTH_INVALID",
                errors={"pin": [f"مطلوب {required_length} أرقام"]},
            )

        user.set_pin(data["pin"])
        user.save(update_fields=["pin_hash", "pin_set_at"])
        return Response({"detail": "تم تعيين رمز الدخول"})


class VerifyPinView(APIView):
    """
    Step-up approval (docs/05).

    A manager enters their PIN to authorize ONE action for the cashier who is
    already logged in. The cashier is never logged out, and both identities are
    recorded against the resulting operation.
    """

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "pos_login"

    @extend_schema(
        summary="Manager step-up approval",
        request=VerifyPinRequestSerializer,
        responses={200: ApprovalTokenSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(VerifyPinRequestSerializer, request)
        context = auth_context(request)

        if context.device_id is None:
            raise PermissionDeniedError(
                "الموافقة بالرمز متاحة من جهاز مفعّل فقط", code="DEVICE_REQUIRED"
            )

        permission = data["permission"]
        if not is_valid_permission(permission):
            raise AppError("صلاحية غير معروفة", code="UNKNOWN_PERMISSION")

        approver = User.objects.filter(
            id=data["user_id"], is_active=True, organization_id=context.organization_id
        ).first()
        if approver is None:
            raise services.AuthenticationFailed()

        if not services.verify_pin(
            user=approver,
            pin=data["pin"],
            device_id=context.device_id,
            ip_address=_client_ip(request),
        ):
            raise services.AuthenticationFailed("رمز الدخول غير صحيح")

        # The approver must actually hold what they are approving. Otherwise
        # any two cashiers could authorize each other.
        approver_permissions = effective_permissions(approver.id, context.branch_id)
        if permission not in approver_permissions and not approver.is_superuser:
            raise PermissionDeniedError(
                f"{approver.full_name_ar} لا يملك صلاحية: {permission}",
                code="APPROVER_LACKS_PERMISSION",
            )

        ttl = resolver.get(
            "security.approval_token_seconds",
            ScopeContext(organization_id=context.organization_id),
        )
        token, expires_in = issue_approval_token(
            permission=permission,
            approver_id=approver.id,
            actor_id=context.user_id,
            target=data.get("target") or None,
            amount=data.get("amount") or None,
            ttl_seconds=ttl,
        )

        logger.info(
            "Step-up approval issued",
            extra={
                "permission": permission,
                "approver_id": str(approver.id),
                "actor_id": str(context.user_id),
                "target": data.get("target"),
            },
        )

        return Response(
            {
                "approval_token": token,
                "expires_in": expires_in,
                "permission": permission,
                "approved_by": approver.full_name_ar,
            }
        )


class MFASetupView(APIView):
    # Reachable with an enrolment token — see PrincipalKind.ENROLLMENT.
    permission_classes = [AllowsEnrollment]
    required_permission = ""

    @extend_schema(
        summary="Begin MFA enrolment",
        request=None,  # no body — the secret is generated server-side
        responses={200: MFASetupSerializer},
    )
    def post(self, request: Request) -> Response:
        user = User.objects.get(id=auth_context(request).user_id)

        if user.mfa_enabled:
            raise AppError("التحقق بخطوتين مفعّل بالفعل", code="MFA_ALREADY_ENABLED")

        secret = totp.generate_secret()
        codes = totp.generate_recovery_codes()

        # Stored but not yet enabled: enrolment only completes once the user
        # proves their authenticator works, so nobody locks themselves out.
        user.mfa_secret = secret
        user.save(update_fields=["mfa_secret"])
        services.issue_recovery_codes(user, codes)

        return Response(
            {
                "secret": secret,
                "provisioning_uri": totp.provisioning_uri(secret, account=user.email),
                "recovery_codes": codes,
            }
        )


class MFAConfirmView(APIView):
    permission_classes = [AllowsEnrollment]
    required_permission = ""

    @extend_schema(
        summary="Confirm MFA enrolment",
        request=MFAConfirmSerializer,
        responses={200: SimpleResultSerializer},
    )
    @transaction.atomic
    def post(self, request: Request) -> Response:
        data = _validated(MFAConfirmSerializer, request)
        user = User.objects.select_for_update().get(id=auth_context(request).user_id)

        if not user.mfa_secret:
            raise AppError("ابدأ التفعيل أولاً", code="MFA_NOT_STARTED")

        if not totp.verify(user.mfa_secret, data["code"]):
            raise services.AuthenticationFailed("رمز التحقق غير صحيح", code="MFA_INVALID")

        user.mfa_enabled = True
        user.mfa_confirmed_at = timezone.now()
        user.save(update_fields=["mfa_enabled", "mfa_confirmed_at"])
        return Response({"detail": "تم تفعيل التحقق بخطوتين"})


class MFADisableView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Disable MFA",
        request=ChangePasswordSerializer,
        responses={200: SimpleResultSerializer, 403: None},
    )
    def post(self, request: Request) -> Response:
        user = User.objects.get(id=auth_context(request).user_id)
        password = request.data.get("current_password", "")

        if not user.check_password(password):
            raise services.AuthenticationFailed("كلمة المرور غير صحيحة")

        # Policy wins over preference: an admin cannot opt out of MFA (C11).
        if services.mfa_is_required(user):
            raise PermissionDeniedError(
                "دورك يتطلب التحقق بخطوتين ولا يمكن إيقافه.",
                code="MFA_REQUIRED_BY_POLICY",
            )

        user.mfa_enabled = False
        user.mfa_secret = ""
        user.mfa_confirmed_at = None
        user.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_confirmed_at"])
        user.recovery_codes.all().delete()
        return Response({"detail": "تم إيقاف التحقق بخطوتين"})


class SessionListView(APIView):
    """Active sessions, so a user can spot a login they do not recognise."""

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(summary="List your active sessions", responses={200: None})
    def get(self, request: Request) -> Response:
        from .models import TokenFamily

        families = TokenFamily.objects.filter(
            user_id=auth_context(request).user_id, revoked_at__isnull=True
        ).order_by("-created_at")[:50]

        return Response(
            {
                "sessions": [
                    {
                        "id": str(f.id),
                        "kind": f.kind,
                        "ip_address": f.ip_address,
                        "user_agent": f.user_agent,
                        "created_at": f.created_at.isoformat(),
                        "last_used_at": f.last_used_at.isoformat() if f.last_used_at else None,
                        "rotation_count": f.rotation_count,
                    }
                    for f in families
                ]
            }
        )

    @extend_schema(
        summary="Revoke all your other sessions", responses={200: SimpleResultSerializer}
    )
    def delete(self, request: Request) -> Response:
        from .models import TokenFamily

        count = TokenFamily.revoke_all_for_user(
            auth_context(request).user_id, reason="USER_REVOKED_ALL"
        )
        return Response({"detail": f"تم إنهاء {count} جلسة"}, status=status.HTTP_200_OK)
