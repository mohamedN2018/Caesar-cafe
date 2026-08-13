from __future__ import annotations

from rest_framework import serializers

from .models import Child, Guardian, IncidentType, PlayArea, PlayIncident, PlayTariff, TariffMode


class PlayAreaSerializer(serializers.ModelSerializer):
    occupancy = serializers.SerializerMethodField()

    class Meta:
        model = PlayArea
        fields = [
            "id",
            "name_ar",
            "max_capacity",
            "min_age_months",
            "max_age_months",
            "requires_socks",
            "billing_variant",
            "socks_variant",
            "notes",
            "is_active",
            "occupancy",
        ]
        read_only_fields = ["id", "occupancy"]

    def get_occupancy(self, area: PlayArea) -> int:
        return area.occupancy()


class PlayTariffSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlayTariff
        fields = [
            "id",
            "area",
            "name_ar",
            "mode",
            "entry_fee",
            "included_minutes",
            "package_minutes",
            "block_minutes",
            "block_rate",
            "grace_minutes",
            "daily_cap",
            "applies_days",
            "applies_from",
            "applies_to",
            "priority",
            "is_default",
            "is_active",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs: dict) -> dict:
        """
        A tariff that cannot charge for an overrun is a tariff that gives the
        time away. Catch it at configuration time, not at the counter.
        """
        mode = attrs.get("mode", getattr(self.instance, "mode", TariffMode.TIMED))
        included = attrs.get("included_minutes", getattr(self.instance, "included_minutes", 0))
        package = attrs.get("package_minutes", getattr(self.instance, "package_minutes", 0))
        block_minutes = attrs.get("block_minutes", getattr(self.instance, "block_minutes", 0))
        block_rate = attrs.get("block_rate", getattr(self.instance, "block_rate", 0))

        if mode == TariffMode.TIMED and included <= 0 and block_minutes <= 0:
            raise serializers.ValidationError(
                {"included_minutes": "تعريفة موقوتة تحتاج فترة مشمولة أو فترة احتساب."}
            )
        if mode == TariffMode.PACKAGE and package <= 0:
            raise serializers.ValidationError({"package_minutes": "الباقة تحتاج مدة محددة."})
        if block_minutes > 0 and block_rate <= 0:
            raise serializers.ValidationError(
                {"block_rate": "فترة احتساب بسعر صفر تعني وقتاً إضافياً مجانياً."}
            )
        return attrs


class GuardianSerializer(serializers.ModelSerializer):
    class Meta:
        model = Guardian
        fields = ["id", "full_name", "phone", "national_id", "visit_count", "notes"]
        read_only_fields = ["id", "visit_count"]


class ChildSerializer(serializers.ModelSerializer):
    class Meta:
        model = Child
        fields = [
            "id",
            "guardian",
            "first_name",
            "birth_date",
            "age_months_snapshot",
            "medical_notes",
            "consent_recorded",
        ]
        read_only_fields = ["id"]


class PlayIncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlayIncident
        fields = [
            "id",
            "area",
            "session",
            "incident_type",
            "description",
            "occurred_at",
            "reported_by",
        ]
        read_only_fields = ["id", "reported_by"]


# ── action payloads ──────────────────────────────────────────────────────────


class CheckInSerializer(serializers.Serializer):
    """
    Deliberately short. A parent with a restless child will not tolerate a long
    form, and every optional field here is one the staff can fill in later.
    """

    area = serializers.UUIDField()
    child_name = serializers.CharField(max_length=100)
    guardian_name = serializers.CharField(max_length=150)
    guardian_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    guardian_id = serializers.UUIDField(required=False, allow_null=True)
    age_months = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    birth_date = serializers.DateField(required=False, allow_null=True)
    tariff = serializers.UUIDField(required=False, allow_null=True)
    tag_number = serializers.CharField(max_length=16)
    order = serializers.UUIDField(required=False, allow_null=True)
    session_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Client-minted, so an offline check-in keeps its identity.",
    )
    medical_notes = serializers.CharField(required=False, allow_blank=True)


class CheckOutSerializer(serializers.Serializer):
    verified = serializers.BooleanField(
        default=False,
        help_text="The staff member confirmed the recipient's identity. Not a formality.",
    )
    released_to_guardian = serializers.UUIDField(required=False, allow_null=True)
    approval_token = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="Step-up token for kids.release_to_other, from POST /auth/approve.",
    )
    bill_to_order = serializers.UUIDField(
        required=False, allow_null=True, help_text="Append to this order; omit to open a new one."
    )
    bill = serializers.BooleanField(default=True)


class OverrideChargeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = serializers.CharField(max_length=200)


class ChangeTariffSerializer(serializers.Serializer):
    tariff = serializers.UUIDField()


class IncidentCreateSerializer(serializers.Serializer):
    area = serializers.UUIDField()
    session = serializers.UUIDField(required=False, allow_null=True)
    incident_type = serializers.ChoiceField(choices=IncidentType.choices)
    description = serializers.CharField()
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)


# ── responses ────────────────────────────────────────────────────────────────


class PlaySessionSerializer(serializers.Serializer):
    """Documents the shape produced by `services.serialize_session` — the one
    payload the live board, the check-out screen and the Z-report all read."""

    id = serializers.UUIDField()
    tag_number = serializers.CharField()
    status = serializers.CharField()
    child_name = serializers.CharField()
    age_months = serializers.IntegerField()
    guardian_name = serializers.CharField()
    guardian_phone = serializers.CharField(allow_blank=True)
    area_id = serializers.UUIDField()
    tariff_name = serializers.CharField()
    checked_in_at = serializers.DateTimeField()
    expected_end_at = serializers.DateTimeField(allow_null=True)
    checked_out_at = serializers.DateTimeField(allow_null=True)
    elapsed_minutes = serializers.IntegerField()
    remaining_minutes = serializers.IntegerField(allow_null=True)
    is_overdue = serializers.BooleanField()
    running_charge = serializers.CharField()
    capped = serializers.BooleanField()
    billable_minutes = serializers.IntegerField()
    computed_charge = serializers.CharField()
    override_charge = serializers.CharField(allow_null=True)
    payable = serializers.CharField()
    order_id = serializers.UUIDField(allow_null=True)
    medical_notes = serializers.CharField(allow_blank=True)


class BoardSerializer(serializers.Serializer):
    area_id = serializers.UUIDField()
    area_name = serializers.CharField()
    occupancy = serializers.IntegerField()
    capacity = serializers.IntegerField()
    sessions = PlaySessionSerializer(many=True)


class CheckInResponseSerializer(serializers.Serializer):
    session = PlaySessionSerializer()
    warnings = serializers.ListField(
        child=serializers.CharField(),
        help_text="Non-blocking problems the staff member must see, e.g. an age outside limits.",
    )


class TariffPreviewSerializer(serializers.Serializer):
    """
    One worked example, computed by the authoritative engine.

    The tariff builder shows these so an admin sees what a rule actually costs
    before saving it. Deliberately a server round-trip: a preview calculated in
    the browser would be a second pricing implementation, and a second
    implementation is how the displayed number and the charged number drift.
    """

    minutes = serializers.IntegerField()
    charge = serializers.CharField()
    billable_minutes = serializers.IntegerField()
    blocks = serializers.IntegerField()
    capped = serializers.BooleanField()


class CheckOutResponseSerializer(serializers.Serializer):
    session = PlaySessionSerializer()
    order_id = serializers.UUIDField(allow_null=True)
    charge = serializers.CharField()
