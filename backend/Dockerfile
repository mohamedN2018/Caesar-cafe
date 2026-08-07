# syntax=docker/dockerfile:1

# ── base ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

# Stub the declared packages so setuptools can resolve them while installing
# dependencies. Real source is copied afterwards, keeping the (slow) dependency
# layer cached across ordinary code changes.
RUN mkdir -p config apps && touch config/__init__.py apps/__init__.py

# ── dev ──────────────────────────────────────────────────────────────────────
FROM base AS dev
RUN pip install --upgrade pip && pip install -e ".[dev]"
COPY . .
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ── prod ─────────────────────────────────────────────────────────────────────
FROM base AS prod
RUN pip install --upgrade pip && pip install -e .
COPY . .

# Run unprivileged: a container compromise should not be a root shell.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/system/health/ || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
