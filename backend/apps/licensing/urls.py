from django.urls import path

from .views import (
    ActivateView,
    DeviceActionView,
    DeviceListView,
    DeviceTokenView,
    HeartbeatView,
    InvoiceBlockView,
    LicenseActionView,
    LicenseDetailView,
    LicenseEventListView,
    LicenseListView,
)

app_name = "licensing"

urlpatterns = [
    # Device-facing
    path("activate/", ActivateView.as_view(), name="activate"),
    path("device-token/", DeviceTokenView.as_view(), name="device-token"),
    path("heartbeat/", HeartbeatView.as_view(), name="heartbeat"),
    path("invoice-blocks/", InvoiceBlockView.as_view(), name="invoice-blocks"),
    # Admin-facing
    path("licenses/", LicenseListView.as_view(), name="license-list"),
    path("licenses/<uuid:pk>/", LicenseDetailView.as_view(), name="license-detail"),
    path("licenses/<uuid:pk>/events/", LicenseEventListView.as_view(), name="license-events"),
    path("licenses/<uuid:pk>/<str:action>/", LicenseActionView.as_view(), name="license-action"),
    path("devices/", DeviceListView.as_view(), name="device-list"),
    path("devices/<uuid:pk>/<str:action>/", DeviceActionView.as_view(), name="device-action"),
]
