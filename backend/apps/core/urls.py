from django.urls import path

from .views import HealthView, SystemInfoView

app_name = "core"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("info/", SystemInfoView.as_view(), name="info"),
]
