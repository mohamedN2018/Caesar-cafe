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

if not ALLOWED_HOSTS or ALLOWED_HOSTS == ["*"]:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set explicitly in production.")

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
