"""
Secret redaction (§42, threat I4).

A redaction policy that is only checked by humans reading code is a policy that
will eventually be violated. These tests push known secret values through the
logging pipeline and assert none survive.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from apps.core.logging import (
    JSONFormatter,
    RedactingFilter,
    RequestIDFilter,
    redact_mapping,
    redact_text,
)

CANARIES = [
    "hunter2SuperSecret",
    "QSR-7X29-K8P4-3F1A",
    "eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIGNATURE",
]


class TestRedactText:
    @pytest.mark.parametrize(
        "raw",
        [
            "password=hunter2SuperSecret",
            "token: eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIGNATURE",
            'license_key="QSR-7X29-K8P4-3F1A"',
            "pin=1234",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIGNATURE",
            "device_secret = abc123def456",
        ],
    )
    def test_secrets_are_masked(self, raw: str) -> None:
        cleaned = redact_text(raw)
        assert "REDACTED" in cleaned
        for canary in CANARIES:
            assert canary not in cleaned
        assert "1234" not in redact_text("pin=1234")

    def test_ordinary_text_is_untouched(self) -> None:
        message = "Order 1024 fired to station COFFEE in 340ms"
        assert redact_text(message) == message


class TestRedactMapping:
    def test_sensitive_keys_are_masked_at_any_depth(self) -> None:
        payload = {
            "user": "ahmed",
            "password": "hunter2SuperSecret",
            "nested": {"api_key": "abc", "safe": "visible"},
            "list": [{"refresh_token": "xyz"}],
        }
        cleaned = redact_mapping(payload)
        assert cleaned["user"] == "ahmed"
        assert cleaned["password"] == "***REDACTED***"
        assert cleaned["nested"]["api_key"] == "***REDACTED***"
        assert cleaned["nested"]["safe"] == "visible"
        assert cleaned["list"][0]["refresh_token"] == "***REDACTED***"

    def test_deep_recursion_is_bounded(self) -> None:
        deep: dict = {"level": 0}
        node = deep
        for i in range(1, 30):
            node["child"] = {"level": i}
            node = node["child"]
        redact_mapping(deep)  # must not blow the stack


class TestEndToEndPipeline:
    def _capture(self, msg, *args) -> str:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(RequestIDFilter())
        handler.addFilter(RedactingFilter())

        logger = logging.getLogger("apps.test.redaction")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info(msg, *args)
        return stream.getvalue()

    def test_no_canary_reaches_the_stream(self) -> None:
        output = self._capture(
            "activation attempt license_key=%s token=%s password=%s",
            "QSR-7X29-K8P4-3F1A",
            "eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIGNATURE",
            "hunter2SuperSecret",
        )
        for canary in CANARIES:
            assert canary not in output, f"{canary} leaked into logs"

    def test_output_is_valid_json_with_a_request_id(self) -> None:
        output = self._capture("plain message")
        record = json.loads(output)
        assert record["msg"] == "plain message"
        assert record["level"] == "INFO"
        assert record["service"] == "api"
        assert "request_id" in record

    def test_extra_fields_are_carried_and_redacted(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(RequestIDFilter())
        handler.addFilter(RedactingFilter())

        logger = logging.getLogger("apps.test.extra")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info(
            "device activated",
            extra={"device_id": "abc-123", "device_secret": "hunter2SuperSecret"},
        )

        record = json.loads(stream.getvalue())
        assert record["device_id"] == "abc-123"
        assert record["device_secret"] == "***REDACTED***"
