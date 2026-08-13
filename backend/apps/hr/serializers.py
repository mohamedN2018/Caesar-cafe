"""
Shapes for the HR API.

Two rules, both the same rule the rest of this codebase follows. **Computed
figures are read-only** — `worked_minutes`, `late_minutes` and the whole
timesheet come from the punches on every read, so a client that tried to write
one is refused rather than accommodated. And **the original punch is always
visible next to the amended one**, because a screen that showed only the
corrected time would make the correction invisible, which is the one thing an
amendment must never be.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import Attendance, AttendanceEvent, WorkPattern, WorkShift


class WorkPatternSerializer(serializers.ModelSerializer):
    crosses_midnight = serializers.BooleanField(read_only=True)
    scheduled_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = WorkPattern
        fields = [
            "id",
            "name_ar",
            "starts_at",
            "ends_at",
            "grace_minutes",
            "crosses_midnight",
            "scheduled_minutes",
            "is_active",
        ]
        read_only_fields = ["id", "crosses_midnight", "scheduled_minutes"]

    def validate(self, attrs):
        """
        A pattern of zero length is a typo, not a shift.

        Equal times would otherwise read as `crosses_midnight` and compute a
        24-hour shift, which is the kind of number that reaches a wage before
        anybody notices it.
        """
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts is not None and ends is not None and starts == ends:
            raise serializers.ValidationError(
                {"ends_at": ["وقت النهاية مثل وقت البداية — الوردية بلا مدة."]}
            )
        return attrs


class WorkShiftSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name_ar", read_only=True)
    pattern_name = serializers.CharField(source="pattern.name_ar", read_only=True)
    starts_at = serializers.TimeField(source="pattern.starts_at", read_only=True)
    ends_at = serializers.TimeField(source="pattern.ends_at", read_only=True)

    class Meta:
        model = WorkShift
        fields = [
            "id",
            "user",
            "user_name",
            "pattern",
            "pattern_name",
            "starts_at",
            "ends_at",
            "business_date",
            "note",
        ]
        read_only_fields = ["id", "user_name", "pattern_name", "starts_at", "ends_at"]


class AttendanceSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name_ar", read_only=True)
    pattern_name = serializers.CharField(
        source="shift.pattern.name_ar", read_only=True, default=None
    )

    #: The effective times — what payroll reads.
    effective_in = serializers.DateTimeField(read_only=True)
    effective_out = serializers.DateTimeField(read_only=True, allow_null=True)

    worked_minutes = serializers.IntegerField(read_only=True, allow_null=True)
    is_open = serializers.BooleanField(read_only=True)
    is_amended = serializers.BooleanField(read_only=True)
    late_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = [
            "id",
            "user",
            "user_name",
            "business_date",
            "shift",
            "pattern_name",
            # The originals stay in the payload beside the effective values. A
            # screen showing only the corrected time hides the correction.
            "checked_in_at",
            "checked_out_at",
            "amended_in_at",
            "amended_out_at",
            "amendment_reason",
            "effective_in",
            "effective_out",
            "worked_minutes",
            "late_minutes",
            "is_open",
            "is_amended",
            "source",
            "note",
        ]
        read_only_fields = fields

    def get_late_minutes(self, obj: Attendance) -> int:
        # The grace comes from the branch, so it is resolved once per request and
        # handed in by the view rather than looked up per row — a settings read
        # per person is a query per person on the one screen that lists everyone.
        grace = self.context.get("grace_minutes")
        if grace is None:
            from . import services

            grace = services.grace_for(obj.shift, services.settings_for(obj.branch))
        return obj.late_minutes(grace_minutes=grace)


class AttendanceEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name_ar", read_only=True, default=None)

    class Meta:
        model = AttendanceEvent
        fields = ["id", "kind", "at", "actor_name", "detail"]
        read_only_fields = fields


class PunchSerializer(serializers.Serializer):
    """
    A manager recording somebody else's punch.

    `user` is required and `at` is optional: the common case is "she is standing
    here now", and making the manager type a timestamp for it would be a step
    that exists only because the API wanted one.
    """

    user = serializers.UUIDField()
    at = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


class AmendSerializer(serializers.Serializer):
    #: Not `required=False`. The reason is the entire point of an amendment —
    #: an unexplained one is indistinguishable from a mistake.
    reason = serializers.CharField(max_length=200)
    checked_in_at = serializers.DateTimeField(required=False, allow_null=True)
    checked_out_at = serializers.DateTimeField(required=False, allow_null=True)


class TimesheetRowSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    name_ar = serializers.CharField()
    scheduled_days = serializers.IntegerField()
    present_days = serializers.IntegerField()
    absent_days = serializers.IntegerField()
    late_days = serializers.IntegerField()
    late_minutes = serializers.IntegerField()
    worked_minutes = serializers.IntegerField()
    overtime_minutes = serializers.IntegerField()
    open_punches = serializers.IntegerField()
