from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AreaViewSet,
    FloorStatusView,
    SessionCloseView,
    SessionTransferView,
    SessionView,
    TableViewSet,
)

app_name = "floor"

router = SimpleRouter()
router.register("areas", AreaViewSet, basename="area")
router.register("tables", TableViewSet, basename="table")

urlpatterns = [
    path("status/", FloorStatusView.as_view(), name="status"),
    path("sessions/", SessionView.as_view(), name="sessions"),
    path("sessions/<uuid:pk>/close/", SessionCloseView.as_view(), name="session-close"),
    path("sessions/<uuid:pk>/transfer/", SessionTransferView.as_view(), name="session-transfer"),
    path("", include(router.urls)),
]
