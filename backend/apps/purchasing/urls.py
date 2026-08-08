from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    GoodsReceiptViewSet,
    OutstandingOrdersView,
    PurchaseOrderViewSet,
    PurchaseReturnViewSet,
    ReorderSuggestionView,
    ValuationView,
)

app_name = "purchasing"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission. OpenAPI already documents the API, so the browsable root would
# only be an unguarded endpoint.
router = SimpleRouter()
router.register("purchase-orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("receipts", GoodsReceiptViewSet, basename="receipt")
router.register("returns", PurchaseReturnViewSet, basename="return")

urlpatterns = [
    path("reorder-suggestions/", ReorderSuggestionView.as_view(), name="reorder-suggestions"),
    path("outstanding/", OutstandingOrdersView.as_view(), name="outstanding"),
    path("valuation/", ValuationView.as_view(), name="valuation"),
    path("", include(router.urls)),
]
