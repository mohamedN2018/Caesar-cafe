"""
Re-sync the system roles with the permission catalogue shipped in this build.

**Why this exists.** `ensure_system_roles` was only ever called from `bootstrap`
(which runs once, for a new cafe) and `seed_demo` (which is for demos). So a
release that added a permission code reached a live cafe with the code in the
catalogue, the routes enforcing it — and no role holding it. The symptom is a
manager who upgraded on Tuesday and cannot open a screen that the release notes
say is theirs, with a 403 naming a permission nobody can find a way to grant.

It was found the ordinary way: the HR codes were added, the API refused a
SUPER_ADMIN, and the roles in the database turned out to predate them.

Safe to run repeatedly, and safe to run on a cafe that has customised its roles.
`ensure_system_roles` compares against `synced_permissions` — the spec as of the
last sync — rather than against what the role currently holds, so it adds what
this build newly introduced and leaves alone anything an operator deliberately
took away. That distinction is the whole reason the column exists.

Belongs in the deploy sequence, after `migrate`. See docs/13.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.authz.services import ensure_system_roles
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = "Grant newly shipped permission codes to the system roles of every organization."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--organization",
            help="Limit to one organization id. Default is every organization.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options) -> None:
        organizations = Organization.objects.all()
        if options["organization"]:
            organizations = organizations.filter(pk=options["organization"])

        if not organizations.exists():
            self.stdout.write(self.style.WARNING("No organizations found — nothing to sync."))
            return

        for organization in organizations:
            before = {
                role.code: set(role.permission_codes)
                for role in organization.roles.filter(is_system=True)
            }

            if options["dry_run"]:
                # Report against the catalogue without touching anything. Reading
                # the shipped spec here rather than calling the service keeps the
                # dry run genuinely read-only — a "dry run" that writes is the
                # kind of thing an operator only discovers afterwards.
                from apps.authz.catalog import SYSTEM_ROLES

                for code, spec in SYSTEM_ROLES.items():
                    missing = set(spec["permissions"]) - before.get(code, set())
                    if missing:
                        self.stdout.write(
                            f"  {organization.name_ar} · {code}: would add {len(missing)} "
                            f"— {', '.join(sorted(missing))}"
                        )
                continue

            roles = ensure_system_roles(organization)
            for code, role in roles.items():
                added = set(role.permission_codes) - before.get(code, set())
                if added:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  {organization.name_ar} · {code}: +{len(added)} "
                            f"— {', '.join(sorted(added))}"
                        )
                    )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
        else:
            # `ensure_system_roles` already invalidates the permission cache, and
            # saying so matters: without it the grant would take effect whenever
            # the cache happened to expire, which is indistinguishable from a bug.
            self.stdout.write(self.style.SUCCESS("Roles synced. Permission cache invalidated."))
