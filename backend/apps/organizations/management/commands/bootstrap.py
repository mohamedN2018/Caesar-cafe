"""
Create the organization, its main branch, the system roles, and an admin.

Idempotent — safe to re-run. This is what the §58 setup wizard calls, and what
a developer runs after `make migrate` to get a working system.
"""

from __future__ import annotations

import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.authz.models import RoleAssignment
from apps.authz.services import ensure_system_roles
from apps.organizations.models import Branch, Organization


class Command(BaseCommand):
    help = "Bootstrap an organization, branch, system roles and an admin user."

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default="كافيه القيصر")
        parser.add_argument("--org-name-en", default="Caesar Cafe")
        parser.add_argument("--branch-name", default="الفرع الرئيسي")
        parser.add_argument("--branch-code", default="MB")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument(
            "--admin-password",
            default=None,
            help="Omit to have a strong one generated and printed once.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["admin_email"].strip().lower()

        organization, org_created = Organization.objects.get_or_create(
            name_ar=options["org_name"],
            defaults={"name_en": options["org_name_en"]},
        )
        self._report("Organization", organization.name_ar, org_created)

        branch, branch_created = Branch.objects.get_or_create(
            organization=organization,
            code=options["branch_code"].upper(),
            defaults={"name_ar": options["branch_name"]},
        )
        self._report("Branch", f"{branch.name_ar} ({branch.code})", branch_created)

        roles = ensure_system_roles(organization)
        self.stdout.write(
            self.style.SUCCESS(f"✓ {len(roles)} system roles ready: {', '.join(sorted(roles))}")
        )

        if User.objects.filter(email=email).exists():
            raise CommandError(
                f"A user with email {email} already exists. "
                "Bootstrap will not modify an existing account."
            )

        password = options["admin_password"] or secrets.token_urlsafe(16)
        admin = User.objects.create_user(
            email=email,
            password=password,
            organization=organization,
            full_name_ar="مدير النظام",
            is_staff=True,
        )
        RoleAssignment.objects.create(user=admin, role=roles["SUPER_ADMIN"], branch=None)
        self._report("Admin", admin.email, True)

        if not options["admin_password"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("  Generated admin password (shown once):"))
            self.stdout.write(self.style.WARNING(f"      {password}"))
            self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "Bootstrap complete. This account holds SUPER_ADMIN, so MFA "
                "enrolment is required at first login (security.require_mfa_for_roles)."
            )
        )

    def _report(self, label: str, value: str, created: bool) -> None:
        mark = "✓ created" if created else "· exists"
        self.stdout.write(self.style.SUCCESS(f"{mark}  {label}: {value}"))
