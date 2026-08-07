from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import CategoryViewSet, ModifierGroupViewSet, ProductViewSet, VariantPriceView

app_name = "catalog"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission. OpenAPI already documents the API, so the browsable root would
# only be an unguarded endpoint.
router = SimpleRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("products", ProductViewSet, basename="product")
router.register("modifier-groups", ModifierGroupViewSet, basename="modifier-group")
router.register("variants", VariantPriceView, basename="variant")

urlpatterns = [path("", include(router.urls))]
