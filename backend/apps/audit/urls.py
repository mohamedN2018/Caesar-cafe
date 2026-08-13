from django.urls import path

from .views import AuditActionsView, AuditLogDetailView, AuditLogListView

app_name = "audit"

# Read-only on purpose. There is no write route, and therefore no route that
# could quietly grow into one (docs/09, T5).
urlpatterns = [
    path("", AuditLogListView.as_view(), name="list"),
    path("actions/", AuditActionsView.as_view(), name="actions"),
    path("<int:pk>/", AuditLogDetailView.as_view(), name="detail"),
]
