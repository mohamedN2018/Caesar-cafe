"""
The licence key is readable in demo mode, and nowhere else.

Two requests that pull against each other: "I don't want the key to disappear or
be hashed from the admin", and "I want a real system". The resolution is not to
pick one — the switch that already publishes the demo staff logins governs this
too.

What must remain true, and is the whole point of these tests: with `DEMO_MODE`
off, **nothing readable is written and nothing readable is served**. A copied
database of a real installation still yields nothing that opens a till, which is
why `key_hash` exists at all.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.licensing import services
from apps.licensing.models import License
from apps.licensing.serializers import LicenseSerializer

pytestmark = pytest.mark.django_db


def issue(organization, branch=None):
    """Issued the same way `test_activation` does, so the two cannot diverge."""
    return services.issue_license(
        organization=organization,
        branch=branch,
        license_type="YEARLY",
        max_devices=3,
        expires_at=timezone.now() + timedelta(days=365),
    )


class TestWithDemoModeOff:
    """The default, and the one that protects a real café."""

    def test_nothing_readable_is_stored(self, settings, organization) -> None:
        settings.DEMO_MODE = False

        issued = issue(organization)

        assert issued.license.key_plaintext == ""

    def test_nothing_readable_is_served(self, settings, organization) -> None:
        settings.DEMO_MODE = False
        issued = issue(organization)

        assert LicenseSerializer(issued.license).data["readable_key"] == ""

    def test_the_key_still_does_not_appear_in_the_row(self, settings, organization) -> None:
        # The original guarantee, restated against the new column: a database
        # copy yields nothing that opens a till.
        settings.DEMO_MODE = False
        issued = issue(organization)
        body = "".join(issued.plaintext_key.split("-")[1:])

        for values in License.objects.values_list(
            "key_hash", "key_prefix", "key_last4", "key_plaintext"
        ):
            joined = " ".join(values)
            assert body not in joined
            assert issued.plaintext_key not in joined

    def test_regenerating_clears_a_key_written_while_demo_mode_was_on(
        self, settings, organization
    ) -> None:
        """
        The migration path nobody would think to test.

        A café that ran as a demo and then went live has readable keys sitting in
        its table. Regenerating is the action an owner takes anyway when they go
        live, and it must clean up rather than preserve.
        """
        settings.DEMO_MODE = True
        issued = issue(organization)
        assert issued.license.key_plaintext

        settings.DEMO_MODE = False
        services.regenerate_key(issued.license, actor=None, ip_address="127.0.0.1")
        issued.license.refresh_from_db()

        assert issued.license.key_plaintext == ""


class TestWithDemoModeOn:
    def test_the_key_is_kept_and_served(self, settings, organization) -> None:
        settings.DEMO_MODE = True

        issued = issue(organization)

        assert issued.license.key_plaintext == issued.plaintext_key
        assert LicenseSerializer(issued.license).data["readable_key"] == issued.plaintext_key

    def test_regenerating_replaces_it_rather_than_appending(self, settings, organization) -> None:
        settings.DEMO_MODE = True
        issued = issue(organization)
        first = issued.license.key_plaintext

        regenerated = services.regenerate_key(issued.license, actor=None, ip_address="127.0.0.1")
        issued.license.refresh_from_db()

        assert issued.license.key_plaintext == regenerated.plaintext_key
        assert issued.license.key_plaintext != first

    def test_the_stored_key_is_the_one_that_activates(self, settings, organization, branch) -> None:
        """
        Readable is only useful if it is also correct.

        A stored key that had drifted from the hash would be worse than no stored
        key: it looks authoritative and opens nothing.
        """
        settings.DEMO_MODE = True
        # A licence with a branch: activation allocates invoice blocks per branch
        # and refuses an org-wide licence, so this one has to be issued like a
        # real till's would be.
        issued = issue(organization, branch)

        activation = services.activate(
            license_key=issued.license.key_plaintext,
            device_name="كاشير الباب",
            branch=None,
            mode="POS",
            platform="web",
            app_version="test",
            fingerprint="",
            ip_address="127.0.0.1",
        )

        assert activation.device_secret


class TestTheSwitchGovernsDisplayToo:
    def test_turning_demo_mode_off_hides_a_key_already_stored(self, settings, organization) -> None:
        """
        Why `readable_key` is a method and not the column.

        Switching the flag off has to hide what was written while it was on,
        immediately and without a data migration — otherwise "off" would mean
        "off for new licences only", which is not what anybody reads it as.
        """
        settings.DEMO_MODE = True
        issued = issue(organization)
        assert LicenseSerializer(issued.license).data["readable_key"]

        settings.DEMO_MODE = False

        assert LicenseSerializer(issued.license).data["readable_key"] == ""
