from __future__ import annotations

from rest_framework import serializers

from .models import Station


class StationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Station
        fields = [
            "id",
            "code",
            "name_ar",
            "target_prep_minutes",
            "auto_accept",
            "printer_name",
            "sort_order",
            "is_active",
        ]
        read_only_fields = ["id"]


class TicketLineSerializer(serializers.Serializer):
    """Documents the shape produced by `services.serialize_ticket`."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    quantity = serializers.CharField()
    modifiers = serializers.ListField(child=serializers.CharField())
    note = serializers.CharField(allow_blank=True)
    ready_at = serializers.DateTimeField(allow_null=True)


class TicketSerializer(serializers.Serializer):
    """
    The kitchen ticket, in ONE shape.

    Deliberately not a ModelSerializer: `services.serialize_ticket` is the
    single source, and both the WebSocket push and this REST fallback emit its
    output verbatim. A KDS that reconnects after a dropped socket must not have
    to parse a different object than the one it was receiving a second earlier —
    two shapes is how a fallback path quietly rots.
    """

    id = serializers.UUIDField()
    ticket_number = serializers.IntegerField()
    status = serializers.CharField()
    order_id = serializers.UUIDField()
    order_number = serializers.CharField()
    order_type = serializers.CharField()
    table = serializers.CharField(allow_null=True)
    station_id = serializers.UUIDField()
    station_name = serializers.CharField()
    target_minutes = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    elapsed_seconds = serializers.IntegerField()
    is_late = serializers.BooleanField()
    lines = TicketLineSerializer(many=True)


class TransitionSerializer(serializers.Serializer):
    """No body — the action is in the URL. Declared so the schema is explicit."""


class RoutingResultSerializer(serializers.Serializer):
    tickets = TicketSerializer(many=True)
    unrouted = serializers.ListField(
        child=serializers.CharField(),
        help_text="Items with no station. Reported, never silently dropped.",
    )
