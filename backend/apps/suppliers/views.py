"""Supplier API: the register, the statement of account, and paying them."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.authz.drf import auth_context
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import Supplier
from .serializers import (
    SupplierPaymentSerializer,
    SupplierSerializer,
    SupplierStatementSerializer,
)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


class SupplierViewSet(BranchScopedViewSet):
    queryset = Supplier.all_objects.all()
    serializer_class = SupplierSerializer
    required_permissions = {
        "GET": "purchasing.view",
        "default": "purchasing.manage_suppliers",
    }
    search_fields = ["name", "phone", "tax_number"]
    ordering_fields = ["name", "current_balance", "created_at"]

    @extend_schema(
        summary="Statement of account",
        request=None,
        responses={200: SupplierStatementSerializer},
    )
    @action(detail=True, methods=["get"])
    def statement(self, request: Request, pk=None) -> Response:
        """
        Every entry, plus a reconciliation of the stored balance against them.

        The drift is reported rather than hidden because a non-zero value is not
        a supplier problem — it is a bug in a write path, and the person looking
        at the statement is the one who will notice first.
        """
        supplier = self.get_object()
        return Response(
            SupplierStatementSerializer(
                {
                    "supplier_id": supplier.id,
                    "supplier_name": supplier.name,
                    "current_balance": supplier.current_balance,
                    "drift": services.reconcile(supplier),
                    "entries": services.statement(supplier),
                }
            ).data
        )

    @extend_schema(
        summary="Record a payment to a supplier",
        request=SupplierPaymentSerializer,
        responses={200: SupplierSerializer},
    )
    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request: Request, pk=None) -> Response:
        """
        Separately permissioned from editing the supplier.

        Keeping a supplier's phone number up to date and moving money out of the
        business are not the same act, and one person often does the first while
        only the owner should do the second.
        """
        principal = auth_context(request)
        if not principal.has("purchasing.pay_supplier"):
            from apps.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError("يتطلب صلاحية: purchasing.pay_supplier")

        serializer = SupplierPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        supplier = self.get_object()
        services.record_payment(
            supplier=supplier,
            amount=serializer.validated_data["amount"],
            reference=serializer.validated_data.get("reference", ""),
            user=_acting_user(request),
        )
        supplier.refresh_from_db()
        return Response(SupplierSerializer(supplier).data)
