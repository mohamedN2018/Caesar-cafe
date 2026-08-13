from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import SupplierViewSet

app_name = "suppliers"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission. OpenAPI already documents the API, so the browsable root would
# only be an unguarded endpoint.
router = SimpleRouter()
router.register("", SupplierViewSet, basename="supplier")

urlpatterns = [path("", include(router.urls))]
