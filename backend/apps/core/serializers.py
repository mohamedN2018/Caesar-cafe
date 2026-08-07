"""Response serializers for system endpoints — these define the API contract."""

from __future__ import annotations

from rest_framework import serializers


class HealthChecksSerializer(serializers.Serializer):
    database = serializers.BooleanField()


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["healthy", "degraded"])
    version = serializers.CharField()
    checks = HealthChecksSerializer()


class DetailSerializer(serializers.Serializer):
    """
    A single human-readable result message.

    Lives here rather than being redefined per app: drf-spectacular keys
    components by class NAME, so two identically-named serializers in different
    apps silently produce a wrong schema.
    """

    detail = serializers.CharField()


class SystemInfoSerializer(serializers.Serializer):
    server_version = serializers.CharField()
    min_supported_client_version = serializers.CharField(
        help_text="Clients below this are refused everything except the heartbeat."
    )
    api_version = serializers.CharField()
