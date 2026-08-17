"""
Backup visibility.

There is a POST to trigger a backup and a GET to see the state of them. There is
deliberately **no restore endpoint and no download endpoint**:

  * Restore replaces the database. An HTTP route that does that is a route
    somebody eventually calls by mistake. It is a management command.
  * A download would stream every order, phone number and staff record over the
    API. The file belongs on the host and in off-site storage, reachable by
    whoever has the encryption key — not to anyone holding a session cookie.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context

from . import backups
from .models import BackupRecord, BackupStatus
from .serializers import BackupRecordSerializer, BackupStatusSerializer


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


class BackupListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "backups.manage"

    @extend_schema(summary="Backups and their state", responses={200: BackupStatusSerializer})
    def get(self, request: Request) -> Response:
        return Response(
            {
                **backups.status(),
                "backups": BackupRecordSerializer(BackupRecord.objects.all()[:50], many=True).data,
            }
        )

    @extend_schema(
        summary="Take a backup now", request=None, responses={201: BackupRecordSerializer}
    )
    def post(self, request: Request) -> Response:
        """
        Runs synchronously.

        A cafe database dumps in seconds, and an operator who pressed this button
        wants the answer, not a task id to go and look up. If it ever grows past
        that, the nightly Celery task is already the async path.
        """
        record = backups.create(user=_acting_user(request))
        return Response(
            BackupRecordSerializer(record).data,
            status=(
                status.HTTP_201_CREATED
                if record.status == BackupStatus.COMPLETE
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
        )


class BackupVerifyView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "backups.manage"

    @extend_schema(summary="Re-digest a backup file", request=None, responses={200: None})
    def post(self, request: Request, pk: int) -> Response:
        record = BackupRecord.objects.filter(id=pk).first()
        if record is None:
            from apps.core.exceptions import NotFoundError

            raise NotFoundError("النسخة غير موجودة", code="BACKUP_NOT_FOUND")

        ok = backups.verify(record)
        return Response(
            {
                "verified": ok,
                "filename": record.filename,
                "note_ar": (
                    "البصمة مطابقة."
                    if ok
                    else "الملف مفقود أو تغيّر — اعتبره غير صالح وخُذ نسخة جديدة."
                ),
            }
        )


class DemoDataStatusSerializer(serializers.Serializer):
    orders = serializers.IntegerField()
    products = serializers.IntegerField()
    open_sessions = serializers.IntegerField()
    job = serializers.DictField(allow_null=True)


class DemoDataSwitchSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["full", "empty"])


class DemoDataView(APIView):
    """
    The demo dataset, switched from a screen.

    Exists so a presentation can show the site in BOTH of its honest states —
    a trading fortnight, and a configured cafe with an empty ledger — without
    anyone at a shell. It is a rebuild, not a visibility toggle, on purpose: a
    "hide the data" switch would leave every report, floor board and kitchen
    screen to individually agree about what is hidden, and the first one that
    forgot would be a screen contradicting the screen beside it.

    `system.settings` gates it: this reissues the licence and kills every
    enrolled device, which is an organisation-level act, not a branch
    preference.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "system.settings"

    @extend_schema(summary="Demo data status", responses={200: DemoDataStatusSerializer})
    def get(self, request: Request) -> Response:
        from django.core.cache import cache

        from apps.catalog.models import Product
        from apps.floor.models import TableSession
        from apps.ops.tasks import DEMO_JOB_KEY
        from apps.orders.models import Order

        principal = auth_context(request)
        return Response(
            {
                "orders": Order.objects.filter(organization_id=principal.organization_id).count(),
                "products": Product.objects.filter(
                    organization_id=principal.organization_id, is_active=True
                ).count(),
                "open_sessions": TableSession.objects.filter(
                    table__area__organization_id=principal.organization_id,
                    closed_at__isnull=True,
                ).count(),
                "job": cache.get(DEMO_JOB_KEY),
            }
        )

    @extend_schema(
        summary="Rebuild the demo data, full or empty",
        request=DemoDataSwitchSerializer,
        responses={202: DemoDataStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        from django.core.cache import cache

        from apps.core.exceptions import ConflictError
        from apps.ops.tasks import DEMO_JOB_KEY, DEMO_JOB_TTL, switch_demo_data

        serializer = DemoDataSwitchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mode = serializer.validated_data["mode"]

        # One rebuild at a time. `cache.add` is atomic — two admins clicking
        # together get one job and one honest 409, not two seeds interleaving
        # deletes through each other's inserts.
        if not cache.add(
            DEMO_JOB_KEY,
            {"state": "queued", "mode": mode, "detail": "", "at": None},
            DEMO_JOB_TTL,
        ):
            current = cache.get(DEMO_JOB_KEY) or {}
            if current.get("state") in ("queued", "running"):
                raise ConflictError("هناك عملية قائمة بالفعل — انتظر انتهاءها.")
            cache.set(
                DEMO_JOB_KEY,
                {"state": "queued", "mode": mode, "detail": "", "at": None},
                DEMO_JOB_TTL,
            )

        switch_demo_data.delay(mode)
        return Response({"queued": mode}, status=status.HTTP_202_ACCEPTED)
