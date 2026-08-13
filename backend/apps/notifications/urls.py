from django.urls import path

from .views import AlertHistoryView, SubscriptionDetailView, SubscriptionView, VapidKeyView

app_name = "notifications"

urlpatterns = [
    path("vapid-key/", VapidKeyView.as_view(), name="vapid-key"),
    path("subscriptions/", SubscriptionView.as_view(), name="subscriptions"),
    path("subscriptions/<uuid:pk>/", SubscriptionDetailView.as_view(), name="subscription-detail"),
    path("alerts/", AlertHistoryView.as_view(), name="alerts"),
]
