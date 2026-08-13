"""Generate the Ed25519 keypair used to sign offline licence tokens."""

import base64

from django.core.management.base import BaseCommand

from apps.licensing.offline_token import generate_keypair


class Command(BaseCommand):
    help = "Generate an Ed25519 keypair for offline licence tokens."

    def handle(self, *args, **options):
        private, public = generate_keypair()

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("LICENSE_SIGNING_KEY (private — server .env only):"))
        self.stdout.write(f"  {base64.b64encode(private).decode()}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Public key (embed in the Desktop binary):"))
        self.stdout.write(f"  {base64.b64encode(public).decode()}")
        self.stdout.write("")
        self.stdout.write(
            "The private key NEVER ships with the client. Rotating it invalidates every\n"
            "outstanding offline token and requires a client update."
        )
