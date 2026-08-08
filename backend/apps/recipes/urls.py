from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import RecipeViewSet, VariantRecipeView

app_name = "recipes"

# SimpleRouter, not DefaultRouter: the latter adds an APIRootView that declares
# no permission. OpenAPI already documents the API, so the browsable root would
# only be an unguarded endpoint.
router = SimpleRouter()
router.register("", RecipeViewSet, basename="recipe")

urlpatterns = [
    path("for-variant/<uuid:variant_id>/", VariantRecipeView.as_view(), name="for-variant"),
    path("", include(router.urls)),
]
