"""System endpoint tests — also the first proof that the envelope works."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# NOTE: `response.data` is the pre-render payload; the envelope is applied by
# the renderer. Success responses must therefore be asserted via .json().
# Error responses are already enveloped by the exception handler, so both work.


class TestHealth:
    def test_returns_healthy_with_a_reachable_database(self, client) -> None:
        response = client.get("/api/v1/system/health/")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "healthy"
        assert body["data"]["checks"]["database"] is True

    def test_is_public(self, client) -> None:
        """The container healthcheck and uptime monitors have no credentials."""
        response = client.get("/api/v1/system/health/")
        assert response.status_code == 200

    def test_response_uses_the_envelope(self, client) -> None:
        body = client.get("/api/v1/system/health/").json()
        assert body["success"] is True
        assert "data" in body
        assert "request_id" in body["meta"]

    def test_request_id_header_matches_the_body(self, client) -> None:
        response = client.get("/api/v1/system/health/")
        assert response["X-Request-ID"] == response.json()["meta"]["request_id"]

    def test_upstream_request_id_is_honoured(self, client) -> None:
        """A proxy-supplied id lets one trace span the proxy and the app."""
        response = client.get("/api/v1/system/health/", HTTP_X_REQUEST_ID="trace-from-proxy")
        assert response["X-Request-ID"] == "trace-from-proxy"


class TestSystemInfo:
    def test_reports_version_negotiation_data(self, client) -> None:
        response = client.get("/api/v1/system/info/")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["api_version"] == "v1"
        assert "min_supported_client_version" in data


class TestErrorEnvelope:
    def test_unknown_path_returns_a_stable_code(self, client) -> None:
        response = client.get("/api/v1/system/nope/")
        assert response.status_code == 404

    def test_unauthenticated_settings_access_is_refused(self, client) -> None:
        response = client.get("/api/v1/settings/schema/")
        assert response.status_code in (401, 403)
        assert response.data["success"] is False
        assert response.data["code"] in ("NOT_AUTHENTICATED", "PERMISSION_DENIED")
