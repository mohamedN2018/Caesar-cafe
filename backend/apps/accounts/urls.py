from django.urls import path

from .views import (
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    MFAConfirmView,
    MFADisableView,
    MFASetupView,
    PosSignInView,
    RefreshView,
    SessionListView,
    SetPinView,
    VerifyPinView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    # The cashier path. A device token plus a PIN or badge — no account.
    path("pos-login/", PosSignInView.as_view(), name="pos-login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("set-pin/", SetPinView.as_view(), name="set-pin"),
    path("verify-pin/", VerifyPinView.as_view(), name="verify-pin"),
    path("sessions/", SessionListView.as_view(), name="sessions"),
    path("mfa/setup/", MFASetupView.as_view(), name="mfa-setup"),
    path("mfa/confirm/", MFAConfirmView.as_view(), name="mfa-confirm"),
    path("mfa/disable/", MFADisableView.as_view(), name="mfa-disable"),
]
