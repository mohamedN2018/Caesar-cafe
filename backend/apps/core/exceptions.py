"""
Consistent API errors.

A raw exception must never reach a client (§41). Every failure becomes the same
envelope with a stable, non-localized `code` for machines and a localized
`message` for humans. Unmapped exceptions become INTERNAL_ERROR and the
traceback goes to logs and Sentry only.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# NOTE: `rest_framework.views` is imported lazily inside the handler below.
# DRF resolves DEFAULT_PERMISSION_CLASSES while `rest_framework.views` is still
# initialising; since our permission classes import this module, a top-level
# import here would be a circular import at startup.


class AppError(APIException):
    """Base for domain errors that carry a stable code."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "APP_ERROR"
    default_detail = "حدث خطأ أثناء تنفيذ العملية"

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        errors: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or self.default_detail)
        if code:
            self.code = code
        # Accepted per-instance because four call sites already read naturally
        # this way, and without it they raised TypeError — turning an intended
        # 403 DEVICE_REVOKED into a 500 with no code, which is exactly the
        # signal the Desktop bootstrap branches on to clear its credentials.
        if status_code is not None:
            self.status_code = status_code
        self.errors = errors or {}
        self.extra = extra or {}


class BusinessRuleError(AppError):
    code = "BUSINESS_RULE_VIOLATION"


class InvalidStateTransition(AppError):
    """Returned as 409 so the client reconciles rather than retrying blindly."""

    status_code = status.HTTP_409_CONFLICT
    code = "INVALID_STATE_TRANSITION"
    default_detail = "لا يمكن تنفيذ هذا الإجراء في الحالة الحالية للطلب"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    default_detail = "تعارض في البيانات"


class NotAuthenticatedError(AppError):
    """401 — no valid credentials. Distinct from 403: log in again vs you cannot."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "NOT_AUTHENTICATED"
    default_detail = "برجاء تسجيل الدخول"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PERMISSION_DENIED"
    default_detail = "ليس لديك صلاحية لتنفيذ هذا الإجراء"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    default_detail = "العنصر المطلوب غير موجود"


class ThrottledError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    default_detail = "عدد كبير من المحاولات. برجاء المحاولة بعد قليل."


# Maps DRF's default codes onto ours so clients see one vocabulary.
_DRF_CODE_MAP = {
    "authentication_failed": "AUTHENTICATION_FAILED",
    "not_authenticated": "NOT_AUTHENTICATED",
    "permission_denied": "PERMISSION_DENIED",
    "not_found": "NOT_FOUND",
    "method_not_allowed": "METHOD_NOT_ALLOWED",
    "throttled": "RATE_LIMITED",
    "parse_error": "MALFORMED_REQUEST",
    "invalid": "VALIDATION_ERROR",
}

_GENERIC_MESSAGES = {
    400: "البيانات المرسلة غير صحيحة",
    401: "برجاء تسجيل الدخول",
    403: "ليس لديك صلاحية لتنفيذ هذا الإجراء",
    404: "العنصر المطلوب غير موجود",
    405: "الإجراء غير مسموح",
    409: "تعارض في البيانات",
    429: "عدد كبير من المحاولات. برجاء المحاولة بعد قليل.",
    500: "حدث خطأ غير متوقع. تم تسجيل المشكلة.",
}


def envelope_exception_handler(exc: Exception, context: dict) -> Response | None:
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, Http404):
        exc = NotFoundError()
    elif isinstance(exc, PermissionDenied):
        exc = PermissionDeniedError()
    elif isinstance(exc, DjangoValidationError):
        exc = AppError(
            "البيانات المرسلة غير صحيحة",
            code="VALIDATION_ERROR",
            errors=getattr(exc, "message_dict", {"detail": list(exc.messages)}),
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        # Unhandled: log with the request id, tell the user nothing internal.
        request = context.get("request")
        logger.exception(
            "Unhandled exception",
            extra={"request_id": getattr(request, "request_id", None)},
        )
        return Response(
            {
                "success": False,
                "message": _GENERIC_MESSAGES[500],
                "code": "INTERNAL_ERROR",
                "errors": {},
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = getattr(exc, "code", None)
    if not code:
        detail_code = getattr(getattr(exc, "detail", None), "code", None)
        code = _DRF_CODE_MAP.get(str(detail_code), "REQUEST_FAILED")

    errors = getattr(exc, "errors", None) or {}
    message = _GENERIC_MESSAGES.get(response.status_code, _GENERIC_MESSAGES[400])

    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        errors = errors or detail
    elif isinstance(detail, str):
        message = detail
    elif isinstance(detail, list) and detail:
        message = str(detail[0])

    payload: dict[str, Any] = {
        "success": False,
        "message": message,
        "code": code,
        "errors": errors,
    }
    extra = getattr(exc, "extra", None)
    if extra:
        payload["detail"] = extra

    response.data = payload
    return response
