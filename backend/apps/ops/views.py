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
from rest_framework import status
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
