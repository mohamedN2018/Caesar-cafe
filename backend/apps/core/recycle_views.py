"""
The recycle bin: what was deleted, and putting it back.

Deleting in this product means deactivating — `is_active = False` — because a
product that has ever been sold must not be removable. That rule is right and it
left a hole: **deactivated rows became invisible.** A category switched off by
accident vanished from every screen, still in the database, with no way back that
did not involve a shell.

Two endpoints, no more. Listing is not paginated by design: this is a bin, and a
bin with hundreds of pages is a sign something is deleting in bulk, which is a
different problem than the one this screen solves.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import auth_context
from apps.core.exceptions import NotFoundError
from apps.core.recycle import BY_KEY, RECYCLABLE, deleted_rows, describe, restore


class DeletedItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    kind = serializers.CharField(help_text="app.Model, e.g. catalog.Product")
    kind_label = serializers.CharField()
    title = serializers.CharField()
    deactivated_at = serializers.DateTimeField(allow_null=True)


class DeletedListSerializer(serializers.Serializer):
    items = DeletedItemSerializer(many=True)
    counts = serializers.DictField(child=serializers.IntegerField())


class RestoreRequestSerializer(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.CharField()


class DeletedItemsView(APIView):
    """Everything deactivated in this organisation, across every model."""

    required_permission = "system.restore"

    @extend_schema(
        summary="List deactivated items",
        description=(
            "Rows deleted through the interface. Deleting deactivates rather than "
            "removes, so every one of these is recoverable."
        ),
        responses={200: DeletedListSerializer},
    )
    def get(self, request: Request) -> Response:
        principal = auth_context(request)

        items: list[dict] = []
        counts: dict[str, int] = {}

        for entry in RECYCLABLE:
            rows = list(deleted_rows(entry, organization_id=principal.organization_id))
            counts[entry.key] = len(rows)
            items.extend(describe(entry, row) for row in rows)

        # Most recently deleted first: somebody opening this screen is nearly
        # always looking for the thing they just removed by mistake.
        #
        # `deactivated_at` can be null on rows switched off before the field
        # existed, and sorting a None against a datetime raises — so nulls sort
        # last rather than crashing the screen that exists to recover them.
        items.sort(
            key=lambda item: (item["deactivated_at"] is not None, item["deactivated_at"]),
            reverse=True,
        )

        return Response({"items": items, "counts": counts})


class RestoreItemView(APIView):
    """Reactivate one row."""

    required_permission = "system.restore"

    @extend_schema(
        summary="Restore a deactivated item",
        request=RestoreRequestSerializer,
        responses={200: DeletedItemSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = RestoreRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]
        row_id = serializer.validated_data["id"]

        entry = BY_KEY.get(kind)
        if entry is None:
            raise NotFoundError("نوع غير معروف", code="UNKNOWN_KIND")

        principal = auth_context(request)

        # Found through the same scoped query the listing uses, not by primary key
        # alone. A restore addressed by id would otherwise reach across tenants —
        # the one place in this module where getting it wrong is not recoverable
        # by deleting again.
        row = (
            deleted_rows(entry, organization_id=principal.organization_id).filter(pk=row_id).first()
        )
        if row is None:
            raise NotFoundError("العنصر غير موجود أو ليس معطَّلاً", code="ITEM_NOT_FOUND")

        restore(entry, row)
        return Response(describe(entry, row))
