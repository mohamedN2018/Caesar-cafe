"""
The reports API.

Every endpoint takes the same `date_from` / `date_to` pair, interpreted as
BUSINESS dates (A5) — so "1st to 7th" means seven trading days, not seven
midnights. Omitted, it defaults to the last 30 days, because an owner opening a
report without setting a range wants to see something.

`?export=csv` returns a file. The permission is checked exactly as for JSON: an
export is not a lesser form of access to the same numbers.

The parameter is `export`, not `format`, because DRF already owns `format` as its
renderer override — asking for `?format=csv` there resolves to a renderer that
does not exist and 404s, which is a confusing way to learn about a name clash.

The classes are written out one by one rather than generated. Generating them
would be shorter and would also hide fifteen endpoints from the schema, from
`tests/test_permission_coverage.py`, and from anyone reading this file to find
out what `/reports/` actually serves.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError
from apps.organizations.models import Branch

from . import business_day, exports, reports, rollups
from .serializers import DashboardSerializer, RollupRebuildSerializer, SalesSummarySerializer

MAX_RANGE_DAYS = 400

DATE_PARAMS = [
    OpenApiParameter("date_from", str, description="Business date, YYYY-MM-DD."),
    OpenApiParameter("date_to", str, description="Business date, YYYY-MM-DD (inclusive)."),
    OpenApiParameter(
        "export", str, enum=["csv"], description="Return a CSV download instead of JSON."
    ),
]


def _branch(request: Request) -> Branch:
    principal = auth_context(request)
    branch = Branch.objects.filter(id=principal.branch_id).first()
    if branch is None:
        raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED", status_code=400)
    return branch


def _range(request: Request, branch) -> tuple[date, date]:
    """
    Parse the requested window, defaulting to the last 30 business days.

    The upper bound exists so one mistyped year cannot ask the server to
    assemble a decade — a report that times out teaches nobody anything.
    """
    today = business_day.today(branch)

    try:
        date_to = (
            date.fromisoformat(request.query_params["date_to"])
            if "date_to" in request.query_params
            else today
        )
        date_from = (
            date.fromisoformat(request.query_params["date_from"])
            if "date_from" in request.query_params
            else date_to - timedelta(days=29)
        )
    except ValueError as exc:
        raise AppError("تاريخ غير صالح — الصيغة YYYY-MM-DD", code="INVALID_DATE") from exc

    if date_from > date_to:
        raise AppError("تاريخ البداية بعد تاريخ النهاية", code="INVALID_RANGE")
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise AppError(
            f"أقصى مدة للتقرير {MAX_RANGE_DAYS} يوم",
            code="RANGE_TOO_LONG",
            extra={"max_days": MAX_RANGE_DAYS},
        )

    return date_from, date_to


class ReportView(APIView):
    """
    Shared plumbing for every dated report.

    Subclasses set `report_key`, `required_permission`, and `compute`. Keeping
    the date parsing here is what stops fifteen endpoints from drifting into
    fifteen slightly different definitions of a date range.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    report_key: str = ""

    @staticmethod
    def compute(branch, date_from, date_to) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def get(self, request: Request):
        branch = _branch(request)
        date_from, date_to = _range(request, branch)
        payload = self.compute(branch, date_from, date_to)

        if request.query_params.get("export") == "csv":
            response = HttpResponse(
                exports.to_csv(self.report_key, payload),
                content_type="text/csv; charset=utf-8",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{exports.filename(self.report_key, date_from, date_to)}"'
            )
            return response

        return Response(payload)


# ── sales ────────────────────────────────────────────────────────────────────


class SalesSummaryView(ReportView):
    report_key = "sales/summary"
    required_permission = "reports.sales"
    compute = staticmethod(reports.sales_summary)

    @extend_schema(
        summary="Sales summary", parameters=DATE_PARAMS, responses={200: SalesSummarySerializer}
    )
    def get(self, request: Request):
        return super().get(request)


class SalesByHourView(ReportView):
    report_key = "sales/by-hour"
    required_permission = "reports.sales"
    compute = staticmethod(reports.sales_by_hour)

    @extend_schema(summary="Sales by hour", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


class SalesByCategoryView(ReportView):
    report_key = "sales/by-category"
    required_permission = "reports.sales"
    compute = staticmethod(reports.sales_by_category)

    @extend_schema(summary="Sales by category", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


class SalesByPaymentMethodView(ReportView):
    report_key = "sales/by-payment-method"
    required_permission = "reports.sales"
    compute = staticmethod(reports.sales_by_payment_method)

    @extend_schema(summary="Sales by payment method", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


# ── products ─────────────────────────────────────────────────────────────────


class ProductsTopView(ReportView):
    report_key = "products/top"
    required_permission = "reports.products"
    compute = staticmethod(reports.products_top)

    @extend_schema(summary="Best and worst sellers", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


class ProductsProfitabilityView(ReportView):
    report_key = "products/profitability"
    required_permission = "reports.products"
    compute = staticmethod(reports.products_profitability)

    @extend_schema(summary="Product profitability", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


# ── inventory ────────────────────────────────────────────────────────────────


class InventoryMovementsView(ReportView):
    report_key = "inventory/movements"
    required_permission = "reports.inventory"
    compute = staticmethod(reports.inventory_movements)

    @extend_schema(summary="Stock movements", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


class InventoryWasteView(ReportView):
    report_key = "inventory/waste"
    required_permission = "reports.inventory"
    compute = staticmethod(reports.inventory_waste)

    @extend_schema(summary="Waste", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


class InventoryVarianceView(ReportView):
    report_key = "inventory/variance"
    required_permission = "reports.inventory"
    compute = staticmethod(reports.inventory_variance)

    @extend_schema(
        summary="Variance — theoretical vs counted", parameters=DATE_PARAMS, responses={200: None}
    )
    def get(self, request: Request):
        return super().get(request)


class PurchasesSummaryView(ReportView):
    report_key = "purchases/summary"
    required_permission = "reports.inventory"
    compute = staticmethod(reports.purchases_summary)

    @extend_schema(summary="Purchases summary", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


# ── people ───────────────────────────────────────────────────────────────────


class EmployeesSalesView(ReportView):
    report_key = "employees/sales"
    required_permission = "reports.employees"
    compute = staticmethod(reports.employees_sales)

    @extend_schema(summary="Sales by employee", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


class EmployeesVoidsView(ReportView):
    report_key = "employees/voids"
    required_permission = "reports.employees"
    compute = staticmethod(reports.employees_voids)

    @extend_schema(
        summary="Void and discount rates per user", parameters=DATE_PARAMS, responses={200: None}
    )
    def get(self, request: Request):
        return super().get(request)


class ShiftVarianceView(ReportView):
    report_key = "shifts/variance"
    required_permission = "reports.financial"
    compute = staticmethod(reports.shift_variance)

    @extend_schema(summary="Cash variance by user", parameters=DATE_PARAMS, responses={200: None})
    def get(self, request: Request):
        return super().get(request)


# ── financial ────────────────────────────────────────────────────────────────


class FinancialPnlView(ReportView):
    report_key = "financial/pnl"
    required_permission = "reports.financial"
    compute = staticmethod(reports.profit_and_loss)

    @extend_schema(
        summary="Net sales − COGS = gross profit", parameters=DATE_PARAMS, responses={200: None}
    )
    def get(self, request: Request):
        return super().get(request)


class SupplierBalancesView(APIView):
    """Not dated — a balance is a position, not a period."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "reports.financial"

    @extend_schema(summary="Supplier balances", responses={200: None})
    def get(self, request: Request):
        branch = _branch(request)
        payload = reports.supplier_balances(branch)

        if request.query_params.get("export") == "csv":
            today = business_day.today(branch)
            response = HttpResponse(
                exports.to_csv("suppliers/balances", payload),
                content_type="text/csv; charset=utf-8",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{exports.filename("suppliers/balances", today, today)}"'
            )
            return response

        return Response(payload)


# ── dashboard & maintenance ──────────────────────────────────────────────────


class DashboardView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "reports.sales"

    @extend_schema(summary="Home dashboard, in one call", responses={200: DashboardSerializer})
    def get(self, request: Request) -> Response:
        return Response(reports.dashboard(_branch(request)))


class RollupRebuildView(APIView):
    """
    Force a rebuild.

    Exists because a rollup is a cache of arithmetic, and a cache you cannot
    rebuild on demand is a liability. Used after a fold fix, or to seed history
    on a fresh deployment.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "reports.financial"

    @extend_schema(
        summary="Rebuild daily rollups for a range",
        request=RollupRebuildSerializer,
        responses={200: None},
    )
    def post(self, request: Request) -> Response:
        payload = RollupRebuildSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        branch = _branch(request)
        date_from = payload.validated_data["date_from"]
        date_to = payload.validated_data["date_to"]

        if (date_to - date_from).days > MAX_RANGE_DAYS:
            raise AppError(f"أقصى مدة {MAX_RANGE_DAYS} يوم", code="RANGE_TOO_LONG")

        return Response({"days_rebuilt": rollups.backfill(branch, date_from, date_to)})
