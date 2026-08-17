"""
Production settings.

The system is internet-facing from Phase 1 (commitment C11), so these are not
"eventually" settings — they apply to the first deployment.

Startup fails loudly if a required secret was not supplied. A misconfigured
production process must not boot into a weak state and serve traffic.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

_REQUIRED = {
    "DJANGO_SECRET_KEY": SECRET_KEY,
    "JWT_SIGNING_KEY": SIMPLE_JWT["SIGNING_KEY"],
    "LICENSE_PEPPER": LICENSE_PEPPER,
    "DATABASE_URL": env("DATABASE_URL"),
}
_PLACEHOLDERS = {"", "insecure-placeholder", "dev-insecure-change-me"}

_missing = [
    name
    for name, value in _REQUIRED.items()
    if not value or str(value) in _PLACEHOLDERS or str(value).startswith("dev-insecure")
]
if _missing:
    raise ImproperlyConfigured(
        "Refusing to start: missing or placeholder production secrets: "
        + ", ".join(sorted(_missing))
    )

# ── the signing key has to be USABLE — so the server makes sure one is ───────
#
# Every other secret above is a random string and any random string does. This
# one is an Ed25519 private key — 32 bytes, standard base64 — and a value that is
# random and wrong is indistinguishable by eye from one that is random and right.
#
# The history, because each step was a real outage:
#
#   1. Validity was not checked at all. A bad key booted, served, and returned
#      500 from the one screen whose job is onboarding a terminal.
#   2. So the boot REFUSED on an invalid key. Then a deploy platform's env panel
#      held a stale, malformed value — the panel keeps its own copy of the env,
#      so no push could correct it — and the whole site could not deploy. The
#      refusal turned a leftover string in a web form into downtime.
#
# Refusing was the means; the end is that activation never fails on a key
# problem. `resolve_signing_key` achieves the end directly: a VALID configured
# value wins (rotation still works), an invalid one is ignored with a CRITICAL
# log, and otherwise the server provisions a key once and persists it in the
# shared `keys` volume — same key across api, worker, beat and restarts.
#
# `keys` imports no models, so this is safe before the app registry loads.
# Importing `services` here instead crash-looped the container on
# `AppRegistryNotReady` — this has to run before apps are ready.
from apps.licensing.keys import resolve_signing_key as _resolve_signing_key

try:
    LICENSE_SIGNING_KEY = _resolve_signing_key(
        LICENSE_SIGNING_KEY, env("LICENSE_KEY_DIR", "/app/keys")
    )
except ValueError as _exc:
    # Only when there is genuinely no path to a stable key: an unwritable key
    # directory with nothing valid configured, or a corrupt persisted file.
    raise ImproperlyConfigured(f"Refusing to start: {_exc}") from _exc

# ── TLS / headers ────────────────────────────────────────────────────────────
# TLS terminates at the reverse proxy; trust its forwarded scheme header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

CORS_ALLOW_ALL_ORIGINS = False

# JSON-only renderer: no browsable API surface in production.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "apps.core.renderers.EnvelopeJSONRenderer",
]

LOG_FORMAT = "json"
LOGGING["handlers"]["console"]["formatter"] = "json"

# ── Sentry (optional) ────────────────────────────────────────────────────────
_sentry_dsn = env("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,  # never ship user data to a third party
        release=APP_VERSION,
    )
