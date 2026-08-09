from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import PrinterViewSet

app_name = "printing"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission, which the route-coverage guard rejects.
router = SimpleRouter()
router.register("", PrinterViewSet, basename="printer")

urlpatterns = [path("", include(router.urls))]
