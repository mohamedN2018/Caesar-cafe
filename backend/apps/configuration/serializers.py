"""
Serializers for the settings API.

These describe the *generic* shape of a registry-driven API: the response keys
are setting keys, so `additionalProperties` (via DictField) is the honest schema
rather than an enumeration that would go stale the moment a setting is added.
"""

from __future__ import annotations

from rest_framework import serializers


class SettingDefinitionSerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.CharField()
    scope = serializers.CharField()
    default = serializers.JSONField(allow_null=True)
    label_ar = serializers.CharField()
    label_en = serializers.CharField(allow_blank=True)
    help_ar = serializers.CharField(allow_blank=True)
    choices = serializers.ListField(child=serializers.CharField())
    permission = serializers.CharField()
    high_impact = serializers.BooleanField(
        help_text="Financial, security or licensing — the UI confirms before saving."
    )
    affects_open_orders = serializers.BooleanField(
        help_text="Applies only to orders opened after the change."
    )
    pushes_to_desktop = serializers.BooleanField()


class SettingSchemaResponseSerializer(serializers.Serializer):
    groups = serializers.DictField(child=SettingDefinitionSerializer(many=True))
    count = serializers.IntegerField()


class ResolvedSettingSerializer(serializers.Serializer):
    value = serializers.JSONField(allow_null=True)
    origin = serializers.ChoiceField(
        choices=["ORGANIZATION", "BRANCH", "DEVICE", "ROLE", "DEFAULT"]
    )
    is_default = serializers.BooleanField()


class SettingListResponseSerializer(serializers.Serializer):
    settings = serializers.DictField(child=ResolvedSettingSerializer())


class SettingWriteRequestSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["ORGANIZATION", "BRANCH", "DEVICE", "ROLE"])
    scope_id = serializers.UUIDField()
    values = serializers.DictField(
        child=serializers.JSONField(),
        help_text="Setting key → new value. Each is validated independently.",
    )


class SettingWriteResponseSerializer(serializers.Serializer):
    applied = serializers.DictField(child=serializers.JSONField())
    errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Keys that failed validation. A 207 means the write was partial.",
    )


class SettingHistoryEntrySerializer(serializers.Serializer):
    key = serializers.CharField()
    scope_type = serializers.CharField()
    scope_id = serializers.UUIDField()
    old_value = serializers.JSONField(allow_null=True)
    new_value = serializers.JSONField(allow_null=True)
    changed_by = serializers.CharField(allow_null=True)
    ip_address = serializers.IPAddressField(allow_null=True)
    created_at = serializers.DateTimeField()


class SettingHistoryResponseSerializer(serializers.Serializer):
    history = SettingHistoryEntrySerializer(many=True)
