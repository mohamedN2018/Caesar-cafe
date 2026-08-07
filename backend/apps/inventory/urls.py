from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AdjustmentView,
    InventoryItemViewSet,
    ReconciliationView,
    StockCountViewSet,
    StockLevelView,
    StockMovementView,
    WasteView,
)

app_name = "inventory"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission. OpenAPI already documents the API, so the browsable root would
# only be an unguarded endpoint.
router = SimpleRouter()
router.register("items", InventoryItemViewSet, basename="item")
router.register("counts", StockCountViewSet, basename="count")

urlpatterns = [
    path("levels/", StockLevelView.as_view(), name="levels"),
    path("movements/", StockMovementView.as_view(), name="movements"),
    path("adjustments/", AdjustmentView.as_view(), name="adjustments"),
    path("waste/", WasteView.as_view(), name="waste"),
    path("reconcile/", ReconciliationView.as_view(), name="reconcile"),
    path("", include(router.urls)),
]
