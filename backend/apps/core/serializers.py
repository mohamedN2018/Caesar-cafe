"""Response serializers for system endpoints — these define the API contract."""

from __future__ import annotations

from rest_framework import serializers


class HealthChecksSerializer(serializers.Serializer):
    database = serializers.BooleanField()


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["healthy", "degraded"])
    version = serializers.CharField()
    checks = HealthChecksSerializer()


class SystemInfoSerializer(serializers.Serializer):
    server_version = serializers.CharField()
    min_supported_client_version = serializers.CharField(
        help_text="Clients below this are refused everything except the heartbeat."
    )
    api_version = serializers.CharField()
