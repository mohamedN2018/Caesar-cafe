from __future__ import annotations

from rest_framework import serializers

from .models import BackupRecord


class BackupRecordSerializer(serializers.ModelSerializer):
    size_mb = serializers.CharField(read_only=True)
    triggered_by_name = serializers.CharField(
        source="triggered_by.full_name_ar", read_only=True, default=None
    )

    class Meta:
        model = BackupRecord
        fields = [
            "id",
            "filename",
            "size_bytes",
            "size_mb",
            "sha256",
            "encrypted",
            "status",
            "error",
            "started_at",
            "finished_at",
            "duration_seconds",
            "triggered_by_name",
        ]
        read_only_fields = fields


class BackupStatusSerializer(serializers.Serializer):
    configured = serializers.BooleanField(
        help_text="True when BACKUP_ENCRYPTION_KEY is set. False means plaintext dumps."
    )
    directory = serializers.CharField()
    total = serializers.IntegerField()
    failed = serializers.IntegerField()
    last_success = serializers.DateTimeField(allow_null=True)
    last_filename = serializers.CharField(allow_null=True)
    last_size_mb = serializers.CharField(allow_null=True)
    hours_since_last = serializers.CharField(
        allow_null=True,
        help_text=(
            "The number that matters. 'last run: COMPLETE' means nothing if the "
            "last run was in April."
        ),
    )
    backups = BackupRecordSerializer(many=True)
