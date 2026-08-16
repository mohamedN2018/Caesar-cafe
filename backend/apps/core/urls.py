from django.urls import path

from .recycle_views import DeletedItemsView, RestoreItemView
from .views import HealthView, SystemInfoView

app_name = "core"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("info/", SystemInfoView.as_view(), name="info"),
    # The recycle bin. Deleting deactivates, so everything here is recoverable —
    # and until now, invisible.
    path("deleted/", DeletedItemsView.as_view(), name="deleted"),
    path("deleted/restore/", RestoreItemView.as_view(), name="restore"),
]
