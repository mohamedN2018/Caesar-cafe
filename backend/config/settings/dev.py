"""Development settings. Never use in production."""

from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

CORS_ALLOW_ALL_ORIGINS = True

# Show the browsable API and Swagger UI locally only.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "apps.core.renderers.EnvelopeJSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

SPECTACULAR_SETTINGS["SERVE_INCLUDE_SCHEMA"] = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
