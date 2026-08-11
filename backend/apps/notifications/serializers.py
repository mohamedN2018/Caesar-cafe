from __future__ import annotations

from rest_framework import serializers

from .models import AlertKind, PushSubscription, SentAlert


class SubscribeSerializer(serializers.Serializer):
    """
    Exactly what `PushSubscription.toJSON()` gives a browser, flattened.

    The three fields are opaque to us and useless apart: `endpoint` names the
    push service, `p256dh` is the key the payload is encrypted to, `auth` salts
    the derivation.
    """

    endpoint = serializers.CharField(max_length=1000)
    p256dh = serializers.CharField(max_length=200)
    auth = serializers.CharField(max_length=100)
    label = serializers.CharField(max_length=120, required=False, allow_blank=True)


class PushSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushSubscription
        fields = ["id", "label", "last_sent_at", "created_at"]
        # `endpoint`, `p256dh` and `auth` are never returned. They are the
        # capability to push to somebody's phone, and a list endpoint that
        # handed them out would let anyone who can read it impersonate us to
        # that device.
        read_only_fields = fields


class VapidKeySerializer(serializers.Serializer):
    """The public half, which the browser needs to create a subscription."""

    public_key = serializers.CharField(allow_null=True)
    configured = serializers.BooleanField()


class SentAlertSerializer(serializers.ModelSerializer):
    kind_label = serializers.SerializerMethodField()

    class Meta:
        model = SentAlert
        fields = ["id", "kind", "kind_label", "title", "body", "url", "delivered", "created_at"]
        read_only_fields = fields

    def get_kind_label(self, alert: SentAlert) -> str:
        return KIND_LABELS.get(alert.kind, alert.kind)


KIND_LABELS = {
    AlertKind.CASH_VARIANCE: "فرق نقدي",
    AlertKind.KITCHEN_LATE: "تأخير في المطبخ",
    AlertKind.KIDS_OVERDUE: "تجاوز وقت طفل",
    AlertKind.TERMINAL_OFFLINE: "جهاز غير متصل",
    AlertKind.BACKUP_FAILED: "فشل نسخة احتياطية",
    AlertKind.SYNC_CONFLICT: "تعارض مزامنة",
    AlertKind.LOW_STOCK: "نقص مخزون",
}
