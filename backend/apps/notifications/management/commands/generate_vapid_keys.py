"""
Generate the VAPID pair, once, per deployment.

Printed rather than written, and never committed (§62). The pair identifies this
server to every push service, and **rotating it invalidates every existing
subscription** — the push service checks each assertion against the key the
subscription was created with — so an owner who regenerates these is telling
every phone in the cafe to enrol again without knowing it.

    python manage.py generate_vapid_keys
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.notifications.webpush import VapidKeys


class Command(BaseCommand):
    help = "Generate a VAPID key pair for Web Push. Prints once; store in .env."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Generate even though a pair exists. Invalidates every subscription.",
        )

    def handle(self, *args, **options):
        existing = getattr(settings, "VAPID_PUBLIC_KEY", "")
        if existing and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    "A VAPID pair is already configured.\n\n"
                    "  Regenerating invalidates EVERY existing push subscription — the push\n"
                    "  service validates each assertion against the key the subscription was\n"
                    "  created with, so every phone would silently stop receiving alerts and\n"
                    "  would have to re-enable notifications by hand.\n\n"
                    "  Re-run with --force only if that is what you intend."
                )
            )
            return

        keys = VapidKeys.generate()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Add these to the server's .env — never to Git:"))
        self.stdout.write("")
        self.stdout.write(f"    VAPID_PRIVATE_KEY={keys.private_key}")
        self.stdout.write(f"    VAPID_PUBLIC_KEY={keys.public_key}")
        self.stdout.write("    VAPID_SUBJECT=mailto:owner@your-cafe.example")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "  VAPID_SUBJECT is how a push service reaches you when deliveries start\n"
                "  failing. Use a real address — it is read exactly once, on the bad day."
            )
        )
        self.stdout.write("")
