"""
Rate limiting (threat D1, and D0 now that the API is internet-facing).

Test settings raise all throttle rates so they do not mask the per-account
lockout tested elsewhere; these tests put a real rate back locally. A limit that
is configured but never exercised is a limit nobody knows is broken.

Note the override mechanism: `override_settings(REST_FRAMEWORK=...)` does NOT
work here. DRF binds `SimpleRateThrottle.THROTTLE_RATES` to the settings dict as
a class attribute at import time, so the class keeps pointing at the original
object. Mutating that dict is what actually changes the rate.
"""

from __future__ import annotations

import pytest
from rest_framework.throttling import SimpleRateThrottle

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_throttle_history():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def strict_login_rate(monkeypatch):
    """Restore the production login rate for the duration of one test."""
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "login", "3/min")
    return 3


class TestLoginThrottle:
    def test_login_is_rate_limited_per_ip(self, make_user, api, strict_login_rate) -> None:
        make_user(email="ahmed@caesar.test")
        payload = {"email": "ahmed@caesar.test", "password": "wrong"}

        statuses = [
            api.post("/api/v1/auth/login/", payload, format="json").status_code
            for _ in range(strict_login_rate + 2)
        ]

        assert 429 in statuses, f"login was never throttled: {statuses}"
        assert statuses.index(429) == strict_login_rate, (
            f"throttle engaged at request {statuses.index(429) + 1}, "
            f"expected after {strict_login_rate}"
        )

    def test_throttle_applies_even_to_correct_credentials(
        self, make_user, api, strict_login_rate
    ) -> None:
        """Otherwise an attacker who guesses right on the last try escapes the limit."""
        make_user(email="ahmed@caesar.test")
        for _ in range(strict_login_rate):
            api.post(
                "/api/v1/auth/login/",
                {"email": "ahmed@caesar.test", "password": "wrong"},
                format="json",
            )

        response = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": "correct-horse-battery"},
            format="json",
        )
        assert response.status_code == 429
        assert response.json()["code"] == "RATE_LIMITED"

    def test_the_fixture_actually_changes_the_rate(self, strict_login_rate) -> None:
        """Guard the guard: a no-op override would make the tests above vacuous."""
        assert SimpleRateThrottle.THROTTLE_RATES["login"] == "3/min"

    def test_rate_is_restored_after_the_override(self) -> None:
        """Runs without the fixture — proves monkeypatch cleaned up."""
        assert SimpleRateThrottle.THROTTLE_RATES["login"] == "10000/min"


class TestPublicEndpointsAreNotThrottledAway:
    def test_health_has_no_throttle(self, api) -> None:
        """A monitor polling every 10s must never be rate-limited off."""
        statuses = {api.get("/api/v1/system/health/").status_code for _ in range(20)}
        assert statuses == {200}


class TestProductionRatesAreConfigured:
    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            ("login", "5/min"),
            ("pos_login", "5/min"),
            ("activation", "5/hour"),
            ("sync_push", "60/min"),
            ("reports", "10/min"),
        ],
    )
    def test_documented_rates_exist_in_base_settings(self, scope: str, expected: str) -> None:
        """docs/09 publishes these numbers; base settings must match."""
        from config.settings.base import REST_FRAMEWORK

        assert REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope] == expected
