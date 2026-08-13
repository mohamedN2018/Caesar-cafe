from django.urls import path

from .views import (
    CashMovementView,
    CloseShiftView,
    CurrentShiftView,
    OpenShiftView,
    ShiftListView,
    XReportView,
    ZReportView,
)

app_name = "shifts"

urlpatterns = [
    path("", ShiftListView.as_view(), name="list"),
    path("open/", OpenShiftView.as_view(), name="open"),
    path("current/", CurrentShiftView.as_view(), name="current"),
    path("<uuid:pk>/cash-movements/", CashMovementView.as_view(), name="cash-movements"),
    path("<uuid:pk>/x-report/", XReportView.as_view(), name="x-report"),
    path("<uuid:pk>/z-report/", ZReportView.as_view(), name="z-report"),
    path("<uuid:pk>/close/", CloseShiftView.as_view(), name="close"),
]
