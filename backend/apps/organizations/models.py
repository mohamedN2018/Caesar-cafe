"""
Organization and Branch — the tenancy spine.

Organization is the tenant boundary (the seam for multi-tenant SaaS later).
Branch is what every business record is scoped to. `branch_id = 1` appears
nowhere; the first deployment is simply one Organization with one Branch.
"""

from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models

from apps.core.models import BaseModel, SoftDeletableModel

branch_code_validator = RegexValidator(
    r"^[A-Z0-9]{2,10}$",
    "Branch code must be 2–10 uppercase letters or digits.",
)


class Organization(BaseModel, SoftDeletableModel):
    name_ar = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    tax_number = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="organizations/", null=True, blank=True)

    class Meta:
        db_table = "organizations"

    def __str__(self) -> str:
        return self.name_ar


class Branch(BaseModel, SoftDeletableModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="branches"
    )
    code = models.CharField(
        max_length=10,
        validators=[branch_code_validator],
        help_text="Appears in order and invoice numbers, e.g. MB.",
    )
    name_ar = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        db_table = "branches"
        verbose_name_plural = "Branches"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_branch_code_per_org"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name_ar} ({self.code})"
