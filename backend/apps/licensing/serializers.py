from __future__ import annotations

from rest_framework import serializers

from .models import Device, License, LicenseEvent, LicenseType


class ActivationRequestSerializer(serializers.Serializer):
    license_key = serializers.CharField(
        help_text="QSR-XXXX-XXXX-XXXX-XXXX. Case and dashes are normalized."
    )
    email = serializers.EmailField()
    device_name = serializers.CharField(max_length=100)
    mode = serializers.ChoiceField(choices=["POS", "KDS", "BOTH"], default="POS")
    platform = serializers.CharField(required=False, allow_blank=True, max_length=64)
    app_version = serializers.CharField(required=False, allow_blank=True, max_length=32)
    fingerprint = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
        help_text="Advisory telemetry. Never used for authorization.",
    )


class ActivationResponseSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    device_secret = serializers.CharField(help_text="Returned once. Store in the OS keychain.")
    offline_token = serializers.CharField(help_text="Ed25519-signed; verified locally.")
    branch_id = serializers.UUIDField()
    branch_name = serializers.CharField()
    device_name = serializers.CharField()
    mode = serializers.CharField()
    license_status = serializers.CharField()
    license_expires_at = serializers.DateTimeField(allow_null=True)


class DeviceTokenRequestSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    device_secret = serializers.CharField(write_only=True)


class HeartbeatRequestSerializer(serializers.Serializer):
    app_version = serializers.CharField(required=False, allow_blank=True, max_length=32)
    pending_operations = serializers.IntegerField(required=False, default=0)


class HeartbeatResponseSerializer(serializers.Serializer):
    server_time = serializers.DateTimeField()
    license_status = serializers.CharField()
    stage = serializers.CharField()
    can_open_new_orders = serializers.BooleanField()
    can_close_open_orders = serializers.BooleanField()
    days_until_expiry = serializers.IntegerField(allow_null=True)
    message_ar = serializers.CharField(allow_blank=True)
    offline_token = serializers.CharField()
    min_supported_client_version = serializers.CharField()
    latest_version = serializers.CharField()


class LicenseSerializer(serializers.ModelSerializer):
    masked_key = serializers.CharField(read_only=True)
    active_device_count = serializers.IntegerField(read_only=True)
    seats_available = serializers.IntegerField(read_only=True)
    branch_name = serializers.CharField(source="branch.name_ar", read_only=True, default=None)

    class Meta:
        model = License
        fields = [
            "id",
            "masked_key",
            "key_prefix",
            "key_last4",
            "customer_email",
            "customer_name",
            "license_type",
            "status",
            "starts_at",
            "expires_at",
            "max_devices",
            "active_device_count",
            "seats_available",
            "activation_count",
            "last_activation_at",
            "branch",
            "branch_name",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class IssuedLicenseSerializer(LicenseSerializer):
    license_key = serializers.CharField(read_only=True)
    warning_ar = serializers.CharField(read_only=True)

    class Meta(LicenseSerializer.Meta):
        fields = [*LicenseSerializer.Meta.fields, "license_key", "warning_ar"]


class IssueLicenseSerializer(serializers.Serializer):
    customer_email = serializers.EmailField()
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    license_type = serializers.ChoiceField(choices=LicenseType.choices)
    max_devices = serializers.IntegerField(min_value=1, max_value=100, default=3)
    expires_at = serializers.DateTimeField(
        required=False, allow_null=True, help_text="Omit for a LIFETIME licence."
    )
    branch_id = serializers.UUIDField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["license_type"] != LicenseType.LIFETIME and not attrs.get("expires_at"):
            raise serializers.ValidationError({"expires_at": ["مطلوب لكل الأنواع عدا LIFETIME"]})
        return attrs


class RenewLicenseSerializer(serializers.Serializer):
    expires_at = serializers.DateTimeField()


class DeviceSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name_ar", read_only=True)
    license_key = serializers.CharField(source="license.masked_key", read_only=True)
    minutes_since_seen = serializers.SerializerMethodField()
    pin_locked = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            "id",
            "device_name",
            "mode",
            "status",
            "platform",
            "app_version",
            "branch",
            "branch_name",
            "license",
            "license_key",
            "first_activated_at",
            "last_seen_at",
            "last_ip",
            "fingerprint_changed_count",
            "minutes_since_seen",
            "pin_locked",
        ]
        read_only_fields = fields

    def get_minutes_since_seen(self, obj) -> float | None:
        return obj.minutes_since_seen()

    def get_pin_locked(self, obj) -> bool:
        """
        Whether PIN sign-in is currently locked on this terminal.

        Surfaced because the lockout was previously invisible: the till said
        "رمز الدخول غير صحيح" to a correct PIN and the devices screen showed a
        perfectly ACTIVE device, so the manager had no way to tell a locked
        terminal from a cashier who had forgotten their PIN. A control with no
        indicator beside it is a control nobody reaches for.
        """
        from apps.accounts.services import is_locked

        return is_locked(f"terminal:{obj.id}")


class LicenseEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name_ar", read_only=True, default=None)
    device_name = serializers.CharField(source="device.device_name", read_only=True, default=None)

    class Meta:
        model = LicenseEvent
        fields = ["id", "event", "actor_name", "device_name", "detail", "ip_address", "created_at"]
        read_only_fields = fields
