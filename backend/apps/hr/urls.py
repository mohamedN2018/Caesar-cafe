from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AmendView,
    AttendanceViewSet,
    PunchView,
    TimesheetView,
    WorkPatternViewSet,
    WorkShiftViewSet,
)

app_name = "hr"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission, and `test_permission_coverage` rightly fails the build for it.
router = SimpleRouter()
router.register("patterns", WorkPatternViewSet, basename="hr-pattern")
router.register("roster", WorkShiftViewSet, basename="hr-shift")
router.register("attendance", AttendanceViewSet, basename="hr-attendance")

urlpatterns = [
    # Before the router, so `attendance/<uuid>/amend/` is not swallowed by the
    # viewset's detail route and answered as a 404 for an unknown action.
    path("attendance/<uuid:pk>/amend/", AmendView.as_view(), name="amend"),
    # The verb is in the path rather than the body: a punch is two different
    # operations with two different failure modes, and one endpoint switching on
    # a field makes "check-out with nothing open" indistinguishable in a log
    # from "check-in twice".
    path("punch/<str:kind>/", PunchView.as_view(), name="punch"),
    path("timesheet/", TimesheetView.as_view(), name="timesheet"),
    path("", include(router.urls)),
]
