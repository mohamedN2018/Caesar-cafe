"""
Root URL configuration.

Everything lives under /api/v1/ so a future v2 can run beside it during a
Desktop rollout — field terminals update on their own schedule and the server
must not break one that has not been upgraded yet.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

api_v1 = [
    path("system/", include("apps.core.urls")),
    path("auth/", include("apps.accounts.urls")),
    path("licensing/", include("apps.licensing.urls")),
    path("catalog/", include("apps.catalog.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("settings/", include("apps.configuration.urls")),
]

urlpatterns = [
    path("api/v1/", include(api_v1)),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
]

if settings.DEBUG:
    urlpatterns += [
        path("admin/", admin.site.urls),
        path(
            "api/v1/docs/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
        path(
            "api/v1/redoc/",
            SpectacularRedocView.as_view(url_name="schema"),
            name="redoc",
        ),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
