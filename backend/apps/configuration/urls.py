from django.urls import path

from .views import (
    SettingDetailView,
    SettingHistoryView,
    SettingListView,
    SettingSchemaView,
)

app_name = "configuration"

urlpatterns = [
    path("schema/", SettingSchemaView.as_view(), name="schema"),
    path("history/", SettingHistoryView.as_view(), name="history"),
    path("", SettingListView.as_view(), name="list"),
    path("<str:key>/", SettingDetailView.as_view(), name="detail"),
]
