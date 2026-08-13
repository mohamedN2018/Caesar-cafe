from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    LineReadyView,
    PerformanceView,
    StationViewSet,
    TicketListView,
    TicketRecallView,
    TicketTransitionView,
)

app_name = "kitchen"

router = SimpleRouter()
router.register("stations", StationViewSet, basename="station")

urlpatterns = [
    path("tickets/", TicketListView.as_view(), name="tickets"),
    path("tickets/<uuid:pk>/recall/", TicketRecallView.as_view(), name="ticket-recall"),
    path(
        "tickets/<uuid:pk>/lines/<uuid:line_id>/ready/",
        LineReadyView.as_view(),
        name="line-ready",
    ),
    path(
        "tickets/<uuid:pk>/<str:action>/",
        TicketTransitionView.as_view(),
        name="ticket-transition",
    ),
    path("performance/", PerformanceView.as_view(), name="performance"),
    path("", include(router.urls)),
]
