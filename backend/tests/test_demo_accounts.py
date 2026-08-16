"""
The demo accounts on the public system-info endpoint.

The seed builds ten staff accounts and prints them to a terminal, which is fine on
a laptop and useless on Dokploy — nobody deploying through a dashboard reads the
container log. So `/system/info/` can offer them to the sign-in screen.

**That endpoint takes no authentication.** A list of working credentials is the
worst possible thing to leak from an unauthenticated route, so the gate is what
these tests are really about: it must be off unless somebody explicitly turned it
on, and it must not be inferable from anything else being on.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.core.views import _DEMO_STAFF
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> Organization:
    return Organization.objects.create(name_ar="كافيه", name_en="Cafe")


def info(client) -> dict:
    response = client.get("/api/v1/system/info/")
    assert response.status_code == 200
    return response.json()["data"]


class TestTheGate:
    def test_no_accounts_are_published_by_default(self, client, settings) -> None:
        """
        The default is off, and this is the test that matters most.

        Everything else here is about correctness; this one is about not handing the
        staff login sheet to an unauthenticated GET.
        """
        settings.DEMO_MODE = False

        assert info(client)["demo_accounts"] == []
        assert info(client)["demo_mode"] is False

    def test_demo_mode_off_hides_accounts_that_exist(self, client, settings, org) -> None:
        """
        The flag decides, not the data.

        A real café that happens to have a user called `cashier@caesar.test` must not
        start publishing its password because the address matched a list.
        """
        settings.DEMO_MODE = False
        User.objects.create_user(
            email="cashier@caesar.test", password="x", organization=org, full_name_ar="منى"
        )

        assert info(client)["demo_accounts"] == []

    def test_the_endpoint_still_answers_with_demo_mode_off(self, client, settings) -> None:
        # The version fields are what Desktop clients negotiate on. Adding the demo
        # block must not have made the endpoint conditional.
        settings.DEMO_MODE = False

        payload = info(client)

        assert payload["api_version"] == "v1"
        assert payload["server_version"]


class TestWhatItPublishes:
    def test_only_accounts_that_actually_exist_are_offered(self, client, settings, org) -> None:
        """
        A button that cannot log in is worse than no button.

        The list is a constant, so an entry for a user somebody deleted would render
        a row that fails on click — and a demo whose own buttons do not work reads as
        a broken product rather than as stale seed data.
        """
        settings.DEMO_MODE = True
        User.objects.create_user(
            email="cashier@caesar.test", password="x", organization=org, full_name_ar="منى"
        )

        offered = {a["email"] for a in info(client)["demo_accounts"]}

        assert offered == {"cashier@caesar.test"}

    def test_an_inactive_account_is_not_offered(self, client, settings, org) -> None:
        settings.DEMO_MODE = True
        user = User.objects.create_user(
            email="cashier@caesar.test", password="x", organization=org, full_name_ar="منى"
        )
        user.is_active = False
        user.save(update_fields=["is_active"])

        assert info(client)["demo_accounts"] == []

    def test_the_superuser_is_offered_without_a_password(self, client, settings, org) -> None:
        """
        `demo_admin` sets that password and `--rotate` changes it.

        This code cannot know the current value, so it sends an empty string rather
        than a guess. Sending a stale `admin` after a rotation would present a dead
        credential as a working one — the button would fail and the operator would
        reasonably conclude the rotation broke the login.
        """
        settings.DEMO_MODE = True
        User.objects.create_superuser(
            email="admin@caesar.deplois.net", password="admin", organization=org
        )

        accounts = info(client)["demo_accounts"]

        assert accounts[0]["email"] == "admin@caesar.deplois.net"
        assert accounts[0]["password"] == ""

    def test_every_offered_account_carries_a_pin_for_the_till(self, client, settings, org) -> None:
        settings.DEMO_MODE = True
        for email, name, _role, _pin in _DEMO_STAFF:
            User.objects.create_user(email=email, password="x", organization=org, full_name_ar=name)

        accounts = [a for a in info(client)["demo_accounts"] if a["password"]]

        assert len(accounts) == len(_DEMO_STAFF)
        assert all(a["pin"] for a in accounts), "a cashier with no PIN cannot reach the keypad"


class TestItDoesNotDriftFromTheSeed:
    def test_the_published_list_matches_what_the_seed_creates(self) -> None:
        """
        The two lists are separate copies and this is what keeps them honest.

        `apps/core/views.py` cannot import a management command — that would drag the
        whole seeding module into every request's import graph — so the emails and
        PINs are duplicated. A copy nobody checks is a copy that drifts, and the
        symptom would be a sign-in button for an account the seed stopped making.
        """
        from apps.organizations.management.commands.seed_demo import STAFF

        published = {(email, pin) for email, _name, _role, pin in _DEMO_STAFF}
        seeded = {(email, pin) for email, _name, _role, pin in STAFF}

        assert published == seeded

    def test_the_published_password_is_the_seeded_one(self) -> None:
        from apps.core.views import _DEMO_PASSWORD
        from apps.organizations.management.commands.seed_demo import DEMO_PASSWORD

        assert _DEMO_PASSWORD == DEMO_PASSWORD
