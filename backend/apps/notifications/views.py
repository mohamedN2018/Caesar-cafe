"""
Subscribing a browser, and seeing what has been sent.

The permission choice worth explaining: subscribing needs **no permission code
at all** beyond being signed in. Anyone with an account may ask to be told about
their own branch, because the alerts contain nothing they cannot already see on
the screens they have — and gating it would mean an owner who added a manager
last week wondering why their phone is silent.

Reading the alert HISTORY is a different matter and needs `reports.sales`: it is
a list of every cash variance and late ticket, which is management information.
"""

from __future__ import annotations

import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, RequiresHuman, auth_context
from apps.core.exceptions import AppError, NotFoundError

from .models import PushSubscription, SentAlert
from .serializers import (
    PushSubscriptionSerializer,
    SentAlertSerializer,
    SubscribeSerializer,
    VapidKeySerializer,
)

logger = logging.getLogger(__name__)


class VapidKeyView(APIView):
    """
    The public application-server key.

    Public by design — it is what `applicationServerKey` needs and it ends up in
    every subscription the browser creates. Knowing it lets somebody create a
    subscription, not read one.
    """

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(summary="The VAPID public key", responses={200: VapidKeySerializer})
    def get(self, request: Request) -> Response:
        public = getattr(settings, "VAPID_PUBLIC_KEY", "")
        return Response({"public_key": public or None, "configured": bool(public)})


class SubscriptionView(APIView):
    """
    A browser asking to be told, and the list of devices already asking.

    Requires a human principal: a device token belongs to a terminal in the
    cafe, and a terminal does not have a pocket to buzz in.
    """

    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman]
    required_permission = ""

    @extend_schema(
        summary="Devices subscribed to alerts",
        responses={200: PushSubscriptionSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        rows = PushSubscription.objects.filter(user_id=principal.user_id)
        return Response(PushSubscriptionSerializer(rows, many=True).data)

    @extend_schema(
        summary="Subscribe this browser",
        request=SubscribeSerializer,
        responses={201: PushSubscriptionSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = SubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        principal = auth_context(request)
        if principal.branch_id is None:
            raise AppError(
                "اختر الفرع أولاً — التنبيهات تخص فرعاً بعينه.",
                code="BRANCH_REQUIRED",
                status_code=400,
            )

        # `endpoint` is unique, so re-subscribing the same browser updates the
        # row rather than growing a second one. Browsers re-issue a subscription
        # whenever their push service rotates, and without this an owner would
        # accumulate a dozen dead endpoints and get one notification per.
        subscription, created = PushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={
                "user_id": principal.user_id,
                "organization_id": principal.organization_id,
                "branch_id": principal.branch_id,
                "p256dh": data["p256dh"],
                "auth": data["auth"],
                "label": data.get("label", ""),
                "failures": 0,
            },
        )

        # `is_new`, not `created`: `created` is a LogRecord attribute and
        # shadowing it raises KeyError from inside the logging call. The
        # architecture guard catches this class — it caught this very line.
        logger.info(
            "Push subscription stored",
            extra={"user": str(principal.user_id), "is_new": created},
        )
        return Response(
            PushSubscriptionSerializer(subscription).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SubscriptionDetailView(APIView):
    """Unsubscribing one device — "stop telling the phone I sold"."""

    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman]
    required_permission = ""

    @extend_schema(summary="Remove a subscription", responses={204: None})
    def delete(self, request: Request, pk) -> Response:
        principal = auth_context(request)
        # Scoped to the caller's own subscriptions: one manager must not be able
        # to silence another's phone.
        deleted, _ = PushSubscription.objects.filter(id=pk, user_id=principal.user_id).delete()
        if not deleted:
            raise NotFoundError("الاشتراك غير موجود", code="SUBSCRIPTION_NOT_FOUND")
        return Response(status=status.HTTP_204_NO_CONTENT)


class AlertHistoryView(APIView):
    """
    What has been raised recently.

    Management information — every cash variance and late ticket — so it needs
    `reports.sales` rather than the bare session that subscribing needs.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "reports.sales"

    @extend_schema(summary="Recent alerts", responses={200: SentAlertSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        rows = SentAlert.objects.filter(branch_id=principal.require_branch())[:100]
        return Response(SentAlertSerializer(rows, many=True).data)
