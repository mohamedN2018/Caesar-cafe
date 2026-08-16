from django.urls import path

from .views import (
    DashboardView,
    EmployeesSalesView,
    EmployeesVoidsView,
    FinancialPnlView,
    InventoryMovementsView,
    InventoryVarianceView,
    InventoryWasteView,
    ProductsProfitabilityView,
    ProductsTopView,
    PurchasesSummaryView,
    RollupRebuildView,
    SalesByCategoryView,
    SalesByChannelView,
    SalesByHourView,
    SalesByPaymentMethodView,
    SalesSummaryView,
    ShiftVarianceView,
    SupplierBalancesView,
)

app_name = "reporting"

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("sales/summary/", SalesSummaryView.as_view(), name="sales-summary"),
    path("sales/by-hour/", SalesByHourView.as_view(), name="sales-by-hour"),
    path("sales/by-category/", SalesByCategoryView.as_view(), name="sales-by-category"),
    path("sales/by-payment-method/", SalesByPaymentMethodView.as_view(), name="sales-by-method"),
    path("sales/by-channel/", SalesByChannelView.as_view(), name="sales-by-channel"),
    path("products/top/", ProductsTopView.as_view(), name="products-top"),
    path(
        "products/profitability/",
        ProductsProfitabilityView.as_view(),
        name="products-profitability",
    ),
    path("inventory/movements/", InventoryMovementsView.as_view(), name="inventory-movements"),
    path("inventory/waste/", InventoryWasteView.as_view(), name="inventory-waste"),
    path("inventory/variance/", InventoryVarianceView.as_view(), name="inventory-variance"),
    path("purchases/summary/", PurchasesSummaryView.as_view(), name="purchases-summary"),
    path("suppliers/balances/", SupplierBalancesView.as_view(), name="supplier-balances"),
    path("employees/sales/", EmployeesSalesView.as_view(), name="employees-sales"),
    path("employees/voids/", EmployeesVoidsView.as_view(), name="employees-voids"),
    path("shifts/variance/", ShiftVarianceView.as_view(), name="shifts-variance"),
    path("financial/pnl/", FinancialPnlView.as_view(), name="financial-pnl"),
    path("rollups/rebuild/", RollupRebuildView.as_view(), name="rollup-rebuild"),
]
