"""
Shared Django settings.

Environment-specific modules (dev/prod/test) import * from here and override.
Nothing secret is defaulted to a usable value — production settings assert that
real values were supplied.
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from celery.schedules import crontab
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env", override=False)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ── Core ─────────────────────────────────────────────────────────────────────
SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-placeholder")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    # local
    "apps.core",
    "apps.organizations",
    "apps.accounts",
    "apps.authz",
    "apps.configuration",
    "apps.licensing",
    "apps.kitchen",
    "apps.inventory",
    "apps.catalog",
    "apps.recipes",
    "apps.suppliers",
    "apps.purchasing",
    "apps.floor",
    "apps.shifts",
    "apps.orders",
    "apps.payments",
    "apps.kids",
    "apps.sync",
    "apps.reporting",
    "apps.audit",
    "apps.ops",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Resolves the principal before any view builds a queryset, so there is no
    # window in which data could be read before we know who is asking.
    "apps.authz.middleware.AuthContextMiddleware",
    # After AuthContextMiddleware: it reads the resolved principal. A reorder
    # would produce audit rows with no actor — the shape you notice only during
    # a dispute. tests/test_audit.py asserts the order.
    "apps.audit.middleware.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ── Database ─────────────────────────────────────────────────────────────────
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", "postgresql://caesar:caesar@postgres:5432/caesar"),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# ── Cache / Channels ─────────────────────────────────────────────────────────
REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

# ── Celery ───────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_TIME_LIMIT = 600
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Nightly rollups. 05:00 local, comfortably after the default 04:00 business-day
# boundary — a job that ran at the boundary itself would race the last order of
# the night. `days=2` re-does the day before yesterday too, which is the cheap
# self-healing: a worker that was down overnight catches up without anyone
# noticing, because rebuilding an existing day costs the same as building it.
CELERY_BEAT_SCHEDULE = {
    # 03:00, BEFORE the 04:00 business-day boundary, so a dump lands while the
    # previous trading day is complete and nothing is half-written into the next.
    "nightly-backup": {
        "task": "ops.nightly_backup",
        "schedule": crontab(hour=3, minute=0),
    },
    "verify-last-backup": {
        "task": "ops.verify_last_backup",
        "schedule": crontab(hour=4, minute=0),
    },
    "nightly-rollups": {
        "task": "reporting.build_rollups",
        "schedule": crontab(hour=5, minute=15),
        "kwargs": {"days": 2},
    },
}

# Where `pg_dump` writes. A volume in production; a temp path in dev, because a
# dev machine should not accumulate database dumps nobody asked for.
BACKUP_DIR = env("BACKUP_DIR", "/backups")

# ── Auth ─────────────────────────────────────────────────────────────────────
# Argon2id first: memory-hard, and the current recommendation for password storage.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── i18n / tz ────────────────────────────────────────────────────────────────
# Storage is always UTC. Display timezone is a runtime setting (org.timezone),
# never a deployment constant — see docs/11-configuration.md.
LANGUAGE_CODE = "ar"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LANGUAGES = [("ar", "العربية"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]

# ── Static / media ───────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── DRF ──────────────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    # Authentication is resolved by AuthContextMiddleware into
    # `request.auth_context`; DRF's own auth layer is not used.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    # Deny by default, and REQUIRE each view to declare a permission code —
    # HasPermission raises if one is missing, so an unguarded endpoint cannot
    # ship silently. See apps/authz/drf.py and docs/05-permissions.md.
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.authz.drf.IsAuthenticatedPrincipal",
        "apps.authz.drf.HasPermission",
    ],
    "DEFAULT_RENDERER_CLASSES": ["apps.core.renderers.EnvelopeJSONRenderer"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.EnvelopeCursorPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "apps.core.exceptions.envelope_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "300/min",
        "login": "5/min",
        "pos_login": "5/min",
        "activation": "5/hour",
        "sync_push": "60/min",
        "reports": "10/min",
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Caesar Cafe API",
    "DESCRIPTION": "POS, kitchen, inventory and management API for كافيه القيصر.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": True,
    # Several models have a `status` field with different choice sets. Without
    # these, generated TypeScript gets meaningless names like `Status7c0Enum`.
    "ENUM_NAME_OVERRIDES": {
        "LicenseStatusEnum": "apps.licensing.models.LicenseStatus.choices",
        "DeviceStatusEnum": "apps.licensing.models.DeviceStatus.choices",
        "LicenseTypeEnum": "apps.licensing.models.LicenseType.choices",
        "DeviceModeEnum": "apps.licensing.models.DeviceMode.choices",
        "OrderStatusEnum": "apps.orders.models.OrderStatus.choices",
        "OrderTypeEnum": "apps.orders.models.OrderType.choices",
        "OrderEventTypeEnum": "apps.orders.models.EventType.choices",
        "OrderItemStatusEnum": "apps.orders.models.ItemStatus.choices",
        "StockMovementTypeEnum": "apps.inventory.models.MovementType.choices",
        "ItemTypeEnum": "apps.inventory.models.ItemType.choices",
        "CostingMethodEnum": "apps.inventory.models.CostingMethod.choices",
        "CountStatusEnum": "apps.inventory.models.CountStatus.choices",
        "CashMovementTypeEnum": "apps.shifts.models.CashMovementType.choices",
        "ShiftStatusEnum": "apps.shifts.models.ShiftStatus.choices",
        "TableStatusEnum": "apps.floor.models.TableStatus.choices",
        "POStatusEnum": "apps.purchasing.models.POStatus.choices",
        "LedgerEntryTypeEnum": "apps.suppliers.models.LedgerEntryType.choices",
        "PlaySessionStatusEnum": "apps.kids.models.SessionStatus.choices",
        "PlayTariffModeEnum": "apps.kids.models.TariffMode.choices",
        "PlayIncidentTypeEnum": "apps.kids.models.IncidentType.choices",
        "SyncStreamEnum": "apps.sync.models.Stream.choices",
        "SyncOperationStatusEnum": "apps.sync.models.OperationStatus.choices",
    },
    # Every response is enveloped by the renderer, so the schema must say so —
    # otherwise the generated TypeScript types describe a payload that never
    # arrives. See apps/core/schema.py.
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.core.schema.envelope_postprocessing_hook",
    ],
}

# ── JWT ──────────────────────────────────────────────────────────────────────
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "SIGNING_KEY": env("JWT_SIGNING_KEY", SECRET_KEY),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_TOKEN_MINUTES", 15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_TOKEN_DAYS", 7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
}

DEVICE_TOKEN_LIFETIME = timedelta(minutes=env_int("JWT_DEVICE_TOKEN_MINUTES", 60))

# ── Licensing ────────────────────────────────────────────────────────────────
LICENSE_PEPPER = env("LICENSE_PEPPER", "")
LICENSE_SIGNING_KEY = env("LICENSE_SIGNING_KEY", "")

# ── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:5173")
CORS_ALLOW_CREDENTIALS = True
FRONTEND_URL = env("FRONTEND_URL", "http://localhost:5173")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOG_FORMAT = env("LOG_FORMAT", "console")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_secrets": {"()": "apps.core.logging.RedactingFilter"},
        "request_id": {"()": "apps.core.logging.RequestIDFilter"},
    },
    "formatters": {
        "console": {
            "format": "{levelname:<8} {asctime} [{request_id}] {name}: {message}",
            "style": "{",
        },
        "json": {"()": "apps.core.logging.JSONFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
            "filters": ["request_id", "redact_secrets"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "apps": {"level": LOG_LEVEL, "propagate": True},
    },
}

APP_VERSION = "0.1.0"
MIN_SUPPORTED_CLIENT_VERSION = "0.1.0"
