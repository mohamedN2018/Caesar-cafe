from django.urls import path

from .views import BackupListView, BackupVerifyView, DemoDataView

app_name = "ops"

# No restore route and no download route — see views.py for why.
urlpatterns = [
    path("backups/", BackupListView.as_view(), name="backups"),
    path("demo-data/", DemoDataView.as_view(), name="demo-data"),
    path("backups/<int:pk>/verify/", BackupVerifyView.as_view(), name="backup-verify"),
]
