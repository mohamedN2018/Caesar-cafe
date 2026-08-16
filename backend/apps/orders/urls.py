from django.urls import path

from .views import (
    OrderDetailView,
    OrderEventView,
    OrderListView,
    OrderReceiptView,
    OrderTypeListView,
    OrderVoidView,
)

app_name = "orders"

urlpatterns = [
    path("", OrderListView.as_view(), name="list"),
    # Before `<uuid:pk>/`, or it would never be reached.
    path("types/", OrderTypeListView.as_view(), name="types"),
    path("<uuid:pk>/", OrderDetailView.as_view(), name="detail"),
    path("<uuid:pk>/events/", OrderEventView.as_view(), name="events"),
    path("<uuid:pk>/void/", OrderVoidView.as_view(), name="void"),
    path("<uuid:pk>/receipt/", OrderReceiptView.as_view(), name="receipt"),
]
