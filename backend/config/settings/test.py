"""Test settings: fast, deterministic, no external services."""

from .base import *

DEBUG = False
# ≥32 bytes: below that PyJWT warns that the HMAC key is short for SHA-256.
SECRET_KEY = "test-only-secret-key-padded-to-thirty-two-bytes-minimum"  # noqa: S105
SIMPLE_JWT = {**SIMPLE_JWT, "SIGNING_KEY": SECRET_KEY}
LICENSE_PEPPER = "test-only-pepper"

# Throttling is per-IP; account lockout is per-account. Both are real controls,
# but in tests they collide — every request comes from the same address, so the
# IP throttle would mask the lockout under test. Rates are raised here and
# exercised explicitly in tests/test_throttling.py with a local override.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        key: "10000/min" for key in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    },
}

# Fast hashing — Argon2 makes a large test suite crawl.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

LOGGING["root"]["level"] = "WARNING"
