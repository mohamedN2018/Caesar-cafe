from django.urls import path

from .views import (
    BranchSyncStatusView,
    ConflictListView,
    ConflictResolveView,
    OperationListView,
    PullView,
    PushView,
    SyncStateView,
)

app_name = "sync"

urlpatterns = [
    path("push/", PushView.as_view(), name="push"),
    path("pull/", PullView.as_view(), name="pull"),
    path("state/", SyncStateView.as_view(), name="state"),
    path("status/", BranchSyncStatusView.as_view(), name="status"),
    path("operations/", OperationListView.as_view(), name="operations"),
    path("conflicts/", ConflictListView.as_view(), name="conflicts"),
    path("conflicts/<uuid:pk>/resolve/", ConflictResolveView.as_view(), name="conflict-resolve"),
]
