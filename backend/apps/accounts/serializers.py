from __future__ import annotations

from rest_framework import serializers


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    mfa_code = serializers.CharField(required=False, allow_blank=True)
    recovery_code = serializers.CharField(required=False, allow_blank=True)


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    access_expires_in = serializers.IntegerField()
    refresh_expires_in = serializers.IntegerField()


class RefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name_ar = serializers.CharField()
    full_name_en = serializers.CharField(allow_blank=True)
    organization_id = serializers.UUIDField(allow_null=True)
    branch_id = serializers.UUIDField(allow_null=True)
    kind = serializers.CharField()
    is_superuser = serializers.BooleanField()
    mfa_enabled = serializers.BooleanField()
    has_pin = serializers.BooleanField()
    permissions = serializers.ListField(child=serializers.CharField())
    roles = serializers.ListField(child=serializers.CharField())


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=10)


class SetPinSerializer(serializers.Serializer):
    pin = serializers.RegexField(r"^\d{4,6}$", write_only=True)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)


class VerifyPinRequestSerializer(serializers.Serializer):
    """Step-up approval: a manager authorizes one action for one target."""

    user_id = serializers.UUIDField(help_text="The approver — the person entering their PIN.")
    pin = serializers.CharField(write_only=True)
    permission = serializers.CharField()
    target = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.CharField(required=False, allow_blank=True)


class ApprovalTokenSerializer(serializers.Serializer):
    approval_token = serializers.CharField()
    expires_in = serializers.IntegerField()
    permission = serializers.CharField()
    approved_by = serializers.CharField()


class MFASetupSerializer(serializers.Serializer):
    secret = serializers.CharField()
    provisioning_uri = serializers.CharField()
    recovery_codes = serializers.ListField(child=serializers.CharField())


class MFAConfirmSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\d{6}$")


class SimpleResultSerializer(serializers.Serializer):
    detail = serializers.CharField()
