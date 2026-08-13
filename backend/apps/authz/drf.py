"""
DRF permission enforcement.

A ViewSet that declares no permission RAISES rather than defaulting to allow or
deny. Defaulting either way is how an endpoint silently ships unguarded — this
way it fails loudly in the first test that touches it, and
`tests/test_permission_coverage.py` enumerates every route to prove none slipped
through.

Client-side checks exist only to shape the UI. This is the real gate.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission

from apps.core.exceptions import NotAuthenticatedError, PermissionDeniedError

from .approval import consume_approval_token
from .context import ANONYMOUS, AuthContext, PrincipalKind

#: Routes intentionally reachable without authentication.
PUBLIC_ROUTE_NAMES = frozenset(
    {
        "core:health",
        "core:info",
        "accounts:login",
        "accounts:refresh",
        "accounts:mfa-verify",
        # A device has no credentials until it activates, and none afterwards
        # until it exchanges its secret. Both are throttled hard instead.
        "licensing:activate",
        "licensing:device-token",
        "schema",
        "swagger-ui",
        "redoc",
    }
)


def auth_context(request) -> AuthContext:
    return getattr(request, "auth_context", None) or ANONYMOUS


class IsAuthenticatedPrincipal(BasePermission):
    """
    Any authenticated principal — web user, device, or device+user.

    Raises 401 explicitly. DRF would otherwise return 403 here, because with no
    DEFAULT_AUTHENTICATION_CLASSES it cannot produce a WWW-Authenticate header
    and so assumes the caller *is* authenticated but unauthorized. The client
    needs the distinction: 401 means "log in again", 403 means "you cannot".
    """

    def has_permission(self, request, view) -> bool:
        context = auth_context(request)
        if not context.is_authenticated:
            raise NotAuthenticatedError()
        if context.kind is PrincipalKind.ENROLLMENT:
            # Password checked, but the account still owes MFA enrolment. This
            # token opens the enrolment endpoints and nothing else.
            raise NotAuthenticatedError(
                "أكمل تفعيل التحقق بخطوتين أولاً", code="MFA_ENROLLMENT_REQUIRED"
            )
        return True


class AllowsEnrollment(BasePermission):
    """
    For the MFA enrolment endpoints only: accepts a normal session OR an
    enrolment token.
    """

    def has_permission(self, request, view) -> bool:
        if not auth_context(request).is_authenticated:
            raise NotAuthenticatedError()
        return True


class RequiresHuman(BasePermission):
    """
    A person must be accountable for this request.

    A bare device token can pull the catalog and drain the outbox; it cannot
    take money, because there would be no one to name in the audit log.
    """

    message = "هذا الإجراء يتطلب تسجيل دخول مستخدم على الجهاز"

    def has_permission(self, request, view) -> bool:
        return auth_context(request).has_human


class HasPermission(BasePermission):
    """
    Checks `view.required_permission`, or `view.required_permissions` per method.

    Accepts a step-up approval token (X-Approval-Token) as an alternative to
    holding the permission directly — that is how a cashier gets a manager's
    approval without logging out. Both identities are recorded.

    **A declaration may be a tuple, meaning "any of these".** Some endpoints are
    genuinely reachable by two unrelated capabilities, and the alternatives are
    both worse: picking one locks out a role that legitimately needs it, and
    inventing a third permission that both roles are granted describes the
    implementation rather than the job. The payment-method list is the case that
    forced it — a cashier needs it to take a payment, an accountant needs it to
    read a report, and neither of those is the other's permission.

    A tuple is `any`, never `all`. An endpoint that truly needs two capabilities
    at once should say so in its own body, where the reason can be written down.
    """

    def has_permission(self, request, view) -> bool:
        required = self._required(request, view)
        if required is None:
            raise ImproperlyConfigured(
                f"{view.__class__.__name__} declares no `required_permission`. "
                "Every endpoint must declare one, or be listed in PUBLIC_ROUTE_NAMES."
            )
        if required == "":  # explicitly public within an otherwise-guarded view
            return True

        options = (required,) if isinstance(required, str) else tuple(required)

        context = auth_context(request)
        if not context.is_authenticated:
            return False
        if any(context.has(code) for code in options):
            return True

        token = request.headers.get("X-Approval-Token")
        if token:
            target = self._target(request, view)
            for code in options:
                approval = consume_approval_token(token, permission=code, target=target)
                if approval:
                    request.approval = approval
                    return True

        # Names the FIRST option, not all of them. A message listing three codes
        # reads as a puzzle; the first is the one the caller was most likely
        # meant to have, and the audit log has the full picture anyway.
        raise PermissionDeniedError(f"ليس لديك صلاحية: {options[0]}", code="PERMISSION_DENIED")

    @staticmethod
    def _required(request, view) -> str | tuple[str, ...] | None:
        per_method = getattr(view, "required_permissions", None)
        if isinstance(per_method, dict):
            return per_method.get(request.method, per_method.get("default"))
        return getattr(view, "required_permission", None)

    @staticmethod
    def _target(request, view) -> str | None:
        pk = (view.kwargs or {}).get("pk")
        if pk is None:
            return None
        model = getattr(getattr(view, "queryset", None), "model", None)
        name = model.__name__.lower() if model else "object"
        return f"{name}:{pk}"


class RequirePermissionMixin:
    """
    Convenience base for views.

        class ProductViewSet(RequirePermissionMixin, ModelViewSet):
            required_permissions = {
                "GET": "catalog.view",
                "POST": "catalog.create",
                "default": "catalog.edit",
            }
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]


class PublicEndpointMixin:
    """Explicitly public. Named so an unguarded endpoint is a visible choice."""

    permission_classes = []
    authentication_classes: list = []
    required_permission = ""


def enforce_permission(context: AuthContext, code: str, *, message: str | None = None) -> None:
    """
    Service-layer check.

    The DRF class answers "may this user ever discount?"; only a service can
    answer "may this user discount THIS order by THIS amount, given it is
    already fired and 40 minutes old?". Rules needing the object live there.
    """
    if not context.has(code):
        raise PermissionDeniedError(message or f"ليس لديك صلاحية: {code}")


def require_kind(context: AuthContext, *kinds: PrincipalKind) -> None:
    if context.kind not in kinds:
        raise PermissionDeniedError("نوع الجلسة غير مسموح لهذا الإجراء")
