from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import User

from . import catalog
from .models import Role, RoleAssignment


class PermissionDefSerializer(serializers.Serializer):
    """The shipped catalogue, so the Web can render a picker it did not invent."""

    code = serializers.CharField()
    group = serializers.CharField()
    label_ar = serializers.CharField()
    description_ar = serializers.CharField()
    sensitive = serializers.BooleanField()


class RoleSerializer(serializers.ModelSerializer):
    # Write-only, and read back in `to_representation`. `Role.permissions` is a
    # related manager of RolePermission rows; letting DRF serialise the attribute
    # directly hands the ListField a manager rather than a list of codes.
    permissions = serializers.ListField(
        child=serializers.CharField(), required=False, write_only=True
    )
    assignment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Role
        fields = [
            "id",
            "code",
            "name_ar",
            "name_en",
            "description_ar",
            "is_system",
            "permissions",
            "assignment_count",
        ]
        read_only_fields = ["id", "is_system", "assignment_count"]

    def to_representation(self, instance: Role) -> dict:
        data = super().to_representation(instance)
        data["permissions"] = sorted(instance.permission_codes)
        return data

    def validate_permissions(self, codes: list[str]) -> list[str]:
        unknown = [code for code in codes if not catalog.is_valid(code)]
        if unknown:
            raise serializers.ValidationError(f"صلاحيات غير معروفة: {', '.join(unknown)}")
        return codes

    def create(self, validated_data: dict) -> Role:
        permissions = validated_data.pop("permissions", [])
        role = Role.objects.create(**validated_data)
        role.set_permissions(permissions)
        return role

    def update(self, instance: Role, validated_data: dict) -> Role:
        permissions = validated_data.pop("permissions", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if permissions is not None:
            # System roles are editable — an owner who does not want cashiers
            # voiding items should be able to say so — but never deletable,
            # because a deleted role orphans every assignment pointing at it.
            instance.set_permissions(permissions)
        return instance


class RoleAssignmentSerializer(serializers.ModelSerializer):
    role_code = serializers.CharField(source="role.code", read_only=True)
    role_name = serializers.CharField(source="role.name_ar", read_only=True)
    branch_name = serializers.CharField(source="branch.name_ar", read_only=True, default=None)

    class Meta:
        model = RoleAssignment
        fields = ["id", "user", "role", "role_code", "role_name", "branch", "branch_name"]
        read_only_fields = ["id", "role_code", "role_name", "branch_name"]


class StaffSerializer(serializers.ModelSerializer):
    assignments = RoleAssignmentSerializer(source="role_assignments", many=True, read_only=True)
    has_pin = serializers.SerializerMethodField()
    job_title = serializers.CharField(source="staff_profile.job_title", read_only=True, default="")

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "phone",
            "full_name_ar",
            "full_name_en",
            "is_active",
            "mfa_enabled",
            "has_pin",
            "pin_set_at",
            "last_login",
            "job_title",
            "assignments",
        ]
        # `pin_hash` is not on this list and never will be. Nor is `password`.
        # A staff screen that could read either would turn one compromised
        # manager session into every terminal in the branch.
        read_only_fields = [
            "id",
            "mfa_enabled",
            "has_pin",
            "pin_set_at",
            "last_login",
            "assignments",
        ]

    def get_has_pin(self, user: User) -> bool:
        return bool(user.pin_hash)


class StaffCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name_ar = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    password = serializers.CharField(min_length=12, write_only=True)
    role = serializers.CharField(
        help_text="A role code, e.g. CASHIER. Assigned to the caller's branch."
    )
    branch_scoped = serializers.BooleanField(
        default=True,
        help_text="False assigns the role across every branch in the organization.",
    )


class ResetPinSerializer(serializers.Serializer):
    """
    An administrative reset, distinct from `POST /auth/set-pin/`.

    Self-service requires the account password because it proves the person at
    the keyboard is the account holder. A manager resetting a cashier's PIN
    cannot know that password — which is the whole reason the reset exists — so
    the proof here is the manager's own permission, and the audit trail records
    who did it to whom.
    """

    pin = serializers.CharField(min_length=4, max_length=8, write_only=True)


class SetActiveSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
