from __future__ import annotations

from rest_framework import serializers

from .models import Stream, SyncConflict, SyncOperation


class OperationSerializer(serializers.Serializer):
    """One item from a device's outbox."""

    op_uuid = serializers.UUIDField(
        help_text="Client-minted and UNIQUE. This field alone is what makes a replay a no-op."
    )
    entity_type = serializers.CharField(max_length=48)
    entity_id = serializers.UUIDField(required=False, allow_null=True)
    payload = serializers.JSONField(default=dict)
    client_seq = serializers.IntegerField(
        required=False, allow_null=True, help_text="The device's outbox ordering."
    )
    aggregate_seq = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="This device's nth operation on this aggregate. Drives SEQUENCE_GAP detection.",
    )
    client_time = serializers.DateTimeField(
        required=False, allow_null=True, help_text="The device's clock. Recorded, never trusted."
    )


class PushRequestSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField(required=False, allow_null=True)
    operations = serializers.ListField(child=OperationSerializer(), min_length=1, max_length=500)


class OperationResultSerializer(serializers.Serializer):
    op_uuid = serializers.UUIDField()
    status = serializers.CharField()
    result = serializers.JSONField(required=False)
    code = serializers.CharField(required=False, allow_null=True)
    server_state = serializers.JSONField(required=False)
    replayed = serializers.BooleanField(required=False)


class PushResponseSerializer(serializers.Serializer):
    applied = serializers.IntegerField()
    failed = serializers.IntegerField()
    results = OperationResultSerializer(many=True)


class ChangeSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    entity_type = serializers.CharField()
    entity_id = serializers.UUIDField()
    operation = serializers.CharField()
    payload = serializers.JSONField()


class PullResponseSerializer(serializers.Serializer):
    stream = serializers.ChoiceField(choices=Stream.choices)
    cursor = serializers.IntegerField(
        help_text="The seq of the last row IN THIS RESPONSE — never the current head."
    )
    has_more = serializers.BooleanField()
    changes = ChangeSerializer(many=True)


class SyncOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncOperation
        fields = [
            "id",
            "op_uuid",
            "batch_id",
            "device",
            "entity_type",
            "entity_id",
            "status",
            "result",
            "error_code",
            "error_message",
            "clock_skew_seconds",
            "received_at",
            "applied_at",
        ]
        read_only_fields = fields


class SyncConflictSerializer(serializers.ModelSerializer):
    entity_type = serializers.CharField(source="operation.entity_type", read_only=True)
    op_uuid = serializers.UUIDField(source="operation.op_uuid", read_only=True)
    device_name = serializers.CharField(source="operation.device.device_name", read_only=True)

    class Meta:
        model = SyncConflict
        fields = [
            "id",
            "operation",
            "op_uuid",
            "entity_type",
            "device_name",
            "code",
            "message_ar",
            "server_state",
            "created_at",
            "resolved_at",
            "resolution",
            "resolution_note",
        ]
        read_only_fields = fields


class ResolveConflictSerializer(serializers.Serializer):
    resolution = serializers.ChoiceField(choices=["ACKNOWLEDGED", "RETRIED", "DISCARDED"])
    note = serializers.CharField(max_length=250, required=False, allow_blank=True)


class DeviceStatusSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    device_name = serializers.CharField()
    status = serializers.CharField()
    app_version = serializers.CharField(allow_blank=True)
    last_seen_at = serializers.DateTimeField(allow_null=True)
    last_push_at = serializers.DateTimeField(allow_null=True)
    pending = serializers.IntegerField()
    rejected = serializers.IntegerField()
    open_conflicts = serializers.IntegerField()
    cursors = serializers.DictField(child=serializers.IntegerField())


class BranchStatusSerializer(serializers.Serializer):
    branch_id = serializers.UUIDField()
    devices = DeviceStatusSerializer(many=True)
    stale_devices = serializers.IntegerField()
    offline_alert_minutes = serializers.IntegerField()
    open_conflicts = serializers.IntegerField()
    heads = serializers.DictField(child=serializers.IntegerField())
