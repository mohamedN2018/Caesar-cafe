"""
Structured logging with secret redaction (§42).

Never logged: passwords, PINs, tokens, license keys, device secrets,
Authorization headers. A policy that is only tested by humans reading code is a
policy that will eventually be violated, so `tests/test_logging_redaction.py`
runs a record containing known secret values and asserts none survive.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .middleware import current_request_id

SENSITIVE_KEYS = (
    "password",
    "passwd",
    "pin",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "license_key",
    "device_secret",
    "signing_key",
    "pepper",
    "refresh",
    "access",
    "cookie",
    "session",
)

REDACTED = "***REDACTED***"

# Catches `password=hunter2`, `"token": "abc"`, `pin: 1234` in free-text messages.
_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(k) for k in SENSITIVE_KEYS) + r")"
    r"(\"?\s*[:=]\s*\"?)([^\s,;&\"'}\)]+)"
)

# `Authorization: Bearer <jwt>` puts the credential one token further along than
# the key=value pattern reaches, so it needs its own rule — and must run first.
_SCHEME_PATTERN = re.compile(r"(?i)\b(bearer|basic|token|apikey)\s+([A-Za-z0-9._\-+/=]{6,})")


def redact_text(text: str) -> str:
    text = _SCHEME_PATTERN.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


def redact_mapping(data: Any, _depth: int = 0) -> Any:
    if _depth > 6:
        return data
    if isinstance(data, dict):
        return {
            key: (
                REDACTED
                if any(s in str(key).lower() for s in SENSITIVE_KEYS)
                else redact_mapping(value, _depth + 1)
            )
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [redact_mapping(item, _depth + 1) for item in data]
    if isinstance(data, str):
        return redact_text(data)
    return data


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        elif isinstance(record.msg, (dict, list)):
            record.msg = redact_mapping(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_mapping(record.args)
            else:
                record.args = tuple(redact_mapping(a) for a in record.args)
        return True


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") or record.request_id is None:
            record.request_id = current_request_id.get() or "-"
        return True


_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "request_id",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    """One JSON object per line, for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": "api",
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or current_request_id.get() or "-",
            "msg": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(redact_mapping(payload), ensure_ascii=False, default=str)
