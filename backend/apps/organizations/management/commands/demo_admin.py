"""
Create or reset the demo administrator.

**This is a demo convenience and it is dangerous on a public host.** It exists
because a reviewer needs to get in and look at a running system without being
handed a password manager, and it prints a warning saying so every time.

**It does not weaken the login path.** `accounts.User` uses email as its username
field, so the identifier is `admin@<domain>` and not the bare word `admin`. Making
a one-word login work would mean changing authentication on an internet-facing
system, which is a much larger and much worse change than typing a longer string
once.

**It does clear mandatory two-factor for the organisation**, through the settings
registry — see `_relax_mfa`. Not a convenience: without it the account it creates
cannot log in at all on a fresh host, because `security.require_mfa_for_roles`
defaults to including SUPER_ADMIN and that is the Phase 2 deadlock. The first
version of this command shipped exactly that, and a test caught it.

Run `--rotate` after the demo to replace the weak password with a strong random
one, printed once. Turning MFA back on is a separate step and docs/15 lists it —
one switch should not quietly do two jobs.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.authz.models import Role, RoleAssignment
from apps.authz.services import ensure_system_roles
from apps.organizations.models import Branch, Organization

#: Fallbacks only. The real values come from `DEMO_ADMIN_EMAIL` and
#: `DEMO_ADMIN_PASSWORD` in the environment, so the address and password can be
#: changed where the rest of the deployment is configured rather than here. These
#: keep the command working on a bare checkout with no `.env` at all.
DEFAULT_EMAIL = "admin@caesar.deplois.net"
DEFAULT_PASSWORD = "admin"  # noqa: S105 — a demo credential, printed on stdout


class Command(BaseCommand):
    help = "Create or reset the demo administrator (SUPER_ADMIN)."

    def add_arguments(self, parser) -> None:
        # `getattr` rather than a direct read: a settings module that predates
        # these keys should fall back, not crash a deploy on start-up.
        parser.add_argument("--email", default=getattr(settings, "DEMO_ADMIN_EMAIL", DEFAULT_EMAIL))
        parser.add_argument(
            "--password", default=getattr(settings, "DEMO_ADMIN_PASSWORD", DEFAULT_PASSWORD)
        )
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Replace the password with a strong random one and print it once.",
        )

    def _relax_mfa(self, organization) -> None:
        """
        Clear mandatory two-factor for this organisation.

        **Without this the demo administrator cannot log in at all.**
        `security.require_mfa_for_roles` defaults to SUPER_ADMIN and
        BRANCH_MANAGER, and it should: those accounts are reachable from the
        internet. But on a fresh host it produces exactly the deadlock Phase 2
        records — login refuses a token until enrolment, and enrolling requires a
        token. The first version of this command shipped that: it created an
        account that answered `MFA_ENROLLMENT_REQUIRED` with no way through. A test
        caught it, which is the only reason it is not in the deploy instructions.

        `seed_demo` does the same thing for the same reason, and both go through
        the settings registry rather than around it, so the change is validated,
        audited, and visible on the settings screen where somebody can see it was
        done.

        **Turning it back on is a separate, documented step**, not something
        `--rotate` does quietly. `--rotate` means "replace the guessable
        password"; making it also demand an authenticator would be one switch
        doing two jobs, and would lock an operator behind enrolment at the moment
        they were trying to secure the host. docs/15 lists both actions.
        """
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        resolver.set_value(
            "security.require_mfa_for_roles",
            [],
            scope=Scope.ORGANIZATION,
            scope_id=organization.id,
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        email: str = options["email"].strip().lower()
        rotate: bool = options["rotate"]
        password: str = secrets.token_urlsafe(18) if rotate else options["password"]

        organization = Organization.objects.order_by("created_at").first()
        if organization is None:
            organization = Organization.objects.create(
                name_ar="كافيه القيصر", name_en="Caesar Cafe"
            )
            self.stdout.write("Created organization كافيه القيصر")

        branch = Branch.objects.filter(organization=organization).order_by("created_at").first()
        if branch is None:
            branch = Branch.objects.create(
                organization=organization, code="MB", name_ar="الفرع الرئيسي"
            )
            self.stdout.write("Created branch الفرع الرئيسي")

        roles: dict[str, Role] = ensure_system_roles(organization)
        self._relax_mfa(organization)

        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(
                email=email,
                password=password,
                organization=organization,
                full_name_ar="مدير النظام",
            )
            created = True
        else:
            user.set_password(password)
            created = False

        # A superuser flag AND the role. The flag short-circuits `can()`; the role
        # is what the permission cache and the audit trail read, and an account
        # holding only the flag looks permission-less on every screen that lists
        # what somebody may do.
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        user.save()

        RoleAssignment.objects.get_or_create(user=user, role=roles["SUPER_ADMIN"], branch=None)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo administrator ready"))
        self.stdout.write("")
        self.stdout.write(f"    email     {user.email}")
        self.stdout.write(f"    password  {password}")
        self.stdout.write(f"    {'created' if created else 'password reset'}")
        self.stdout.write("")

        if rotate:
            self.stdout.write(
                self.style.WARNING(
                    "  Shown once. Store it now — it is not recoverable from the database."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  ⚠ This password is guessable and this host is on the public internet.\n"
                    "    Anyone who finds the domain owns the cafe's money, staff records and\n"
                    "    audit trail. Acceptable while somebody is looking at a demo; not\n"
                    "    acceptable once it is left running.\n"
                    "\n"
                    "    Close it with one command:\n"
                    "        python manage.py demo_admin --rotate"
                )
            )
        self.stdout.write("")
