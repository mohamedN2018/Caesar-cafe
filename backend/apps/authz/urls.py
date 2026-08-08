from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import PermissionCatalogView, RoleViewSet, StaffViewSet

app_name = "authz"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission. OpenAPI already documents the API, so the browsable root would
# only be an unguarded endpoint.
router = SimpleRouter()
router.register("staff", StaffViewSet, basename="staff")
router.register("roles", RoleViewSet, basename="role")

urlpatterns = [
    path("permissions/", PermissionCatalogView.as_view(), name="permissions"),
    path("", include(router.urls)),
]
