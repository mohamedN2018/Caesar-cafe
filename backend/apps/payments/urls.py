from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    InvoiceDetailView,
    InvoiceListView,
    PaymentMethodViewSet,
    PaymentView,
    RefundView,
)

app_name = "payments"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission, which the route-coverage guard rejects.
router = SimpleRouter()
router.register("methods", PaymentMethodViewSet, basename="method")

urlpatterns = [
    path("", PaymentView.as_view(), name="list"),
    path("refunds/", RefundView.as_view(), name="refunds"),
    path("invoices/", InvoiceListView.as_view(), name="invoices"),
    path("invoices/<uuid:pk>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("", include(router.urls)),
]
