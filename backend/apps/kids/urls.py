from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    BoardView,
    ChangeTariffView,
    CheckInView,
    CheckOutView,
    ChildViewSet,
    GuardianViewSet,
    IncidentView,
    KidsReportView,
    OverrideChargeView,
    PlayAreaViewSet,
    PlayIncidentViewSet,
    PlayTariffViewSet,
    SessionListView,
    TariffPreviewView,
)

app_name = "kids"

router = SimpleRouter()
router.register("areas", PlayAreaViewSet, basename="area")
router.register("tariffs", PlayTariffViewSet, basename="tariff")
router.register("guardians", GuardianViewSet, basename="guardian")
router.register("children", ChildViewSet, basename="child")
router.register("incidents", PlayIncidentViewSet, basename="incident")

urlpatterns = [
    path("areas/<uuid:area_id>/board/", BoardView.as_view(), name="board"),
    path("tariffs/<uuid:pk>/preview/", TariffPreviewView.as_view(), name="tariff-preview"),
    path("sessions/", SessionListView.as_view(), name="sessions"),
    path("sessions/check-in/", CheckInView.as_view(), name="check-in"),
    path("sessions/<uuid:pk>/check-out/", CheckOutView.as_view(), name="check-out"),
    path("sessions/<uuid:pk>/override/", OverrideChargeView.as_view(), name="override"),
    path("sessions/<uuid:pk>/tariff/", ChangeTariffView.as_view(), name="change-tariff"),
    path("incidents/log/", IncidentView.as_view(), name="log-incident"),
    path("reports/", KidsReportView.as_view(), name="reports"),
    path("", include(router.urls)),
]
