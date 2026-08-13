"""Printer registry API."""

from __future__ import annotations

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from apps.authz.drf import auth_context
from apps.core.viewsets import BranchScopedViewSet

from .models import Printer
from .serializers import PrinterSerializer


class PrinterViewSet(BranchScopedViewSet):
    queryset = Printer.all_objects.prefetch_related("stations")
    serializer_class = PrinterSerializer
    required_permissions = {"GET": "floor.view", "default": "branch.manage_printers"}
    filterset_fields = ["kind", "is_active"]
    pagination_class = None

    @transaction.atomic
    def perform_create(self, serializer) -> None:
        data = serializer.validated_data
        self._clear_the_slot(data.get("is_default"), data.get("kind"), exclude=None)
        super().perform_create(serializer)

    @transaction.atomic
    def perform_update(self, serializer) -> None:
        data = serializer.validated_data
        instance = serializer.instance
        self._clear_the_slot(
            data.get("is_default", instance.is_default),
            # A PATCH that only flips the flag never mentions the kind.
            data.get("kind", instance.kind),
            exclude=instance.pk,
        )
        super().perform_update(serializer)

    def _clear_the_slot(self, wants_default, kind, *, exclude) -> None:
        """
        One default per kind, enforced by MOVING the flag rather than refusing.

        A database constraint alone would make "make this the default" fail with
        a uniqueness error, which is a correct database and a useless interface:
        the person pressing it has already decided.

        Two details that look like fussiness and are not:

          * this runs BEFORE the save. The unique index is checked per
            statement, so demoting afterwards means the insert has already
            collided and the caller gets a 500 instead of the thing they asked
            for;

          * it saves each demoted row instead of one `.update()`. A queryset
            update fires no signals, so the change log would never learn about
            the demotion and every terminal would go on believing the old
            printer is still the default — two defaults on the till, and which
            one wins becomes a matter of row order.
        """
        if not wants_default:
            return

        principal = auth_context(self.request)
        held = Printer.objects.filter(branch_id=principal.branch_id, kind=kind, is_default=True)
        if exclude is not None:
            held = held.exclude(pk=exclude)

        for printer in held:
            printer.is_default = False
            printer.updated_by_id = principal.user_id
            printer.save(update_fields=["is_default", "updated_by", "updated_at"])

    @extend_schema(
        summary="Printers a terminal should know about",
        responses={200: PrinterSerializer(many=True)},
    )
    def list(self, request: Request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    def perform_destroy(self, instance: Printer) -> None:
        """
        Deactivated, not deleted. A queued job may still name it, and a job
        pointing at a row that no longer exists is a receipt that vanishes
        without saying why.
        """
        from django.utils import timezone

        instance.is_active = False
        instance.is_default = False
        instance.deactivated_at = timezone.now()
        instance.updated_by_id = auth_context(self.request).user_id
        instance.save(
            update_fields=["is_active", "is_default", "deactivated_at", "updated_by", "updated_at"]
        )
