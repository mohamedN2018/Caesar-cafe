from __future__ import annotations

from rest_framework import serializers

from .actions import ACTIONS
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    label_ar = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "label_ar",
            "domain",
            "severity",
            "branch",
            "actor",
            "actor_name",
            "approved_by_name",
            "device_id",
            "object_type",
            "object_id",
            "object_label",
            "before",
            "after",
            "changes",
            "detail",
            "ip_address",
            "request_id",
            "occurred_at",
        ]
        read_only_fields = fields

    def get_label_ar(self, row: AuditLog) -> str:
        from .actions import BY_CODE

        action = BY_CODE.get(row.action)
        return action.label_ar if action else row.action


class AuditActionSerializer(serializers.Serializer):
    """The catalogue, so the UI can build its filter without hardcoding codes."""

    code = serializers.CharField()
    domain = serializers.CharField()
    label_ar = serializers.CharField()
    severity = serializers.CharField()


def catalogue() -> list[dict]:
    return [
        {
            "code": action.code,
            "domain": action.domain,
            "label_ar": action.label_ar,
            "severity": action.severity,
        }
        for action in ACTIONS
    ]
