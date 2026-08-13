# One compose file for local and production; `.env` decides which. No -f flag,
# so `make` and a bare `docker compose` can never disagree about the target.
COMPOSE := docker compose

# pytest, ruff and mypy are dev extras and are NOT in the production image — a
# deployed container that ships a test runner and a compiler is a larger attack
# surface for nothing. So the tooling targets pin the dev image rather than
# inheriting whatever `.env` happens to say, which otherwise fails with a bare
# "No module named pytest" the first time somebody runs `make test` after a
# production check.
# `--build` because there is no bind mount any more: it could not survive into a
# shared file (mounting the host's ./backend over /app in production would shadow
# the image's installed code), so the container runs what was built. Without it
# `make test` runs the code from the last build and reports on a file you edited
# ten minutes ago — passing, or failing, for the wrong reason. The layer cache
# makes it cheap; only the COPY layer onwards is redone.
#
# For an editing loop, `docker compose watch` syncs continuously instead.
DEV := DJANGO_ENV=dev docker compose

.DEFAULT_GOAL := help
.PHONY: help up down restart logs build ps migrate makemigrations superuser shell shell-db \
        test test-fast lint format typecheck check schema seed clean

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up:  ## Start the development stack
	$(COMPOSE) up -d

down:  ## Stop the stack
	$(COMPOSE) down

restart:  ## Restart the API container
	$(COMPOSE) restart api

build:  ## Rebuild images
	$(COMPOSE) build

ps:  ## Show container status
	$(COMPOSE) ps

logs:  ## Tail API logs
	$(COMPOSE) logs -f api

migrate:  ## Apply migrations
	$(COMPOSE) run --rm api python manage.py migrate

makemigrations:  ## Generate migrations
	$(COMPOSE) run --rm api python manage.py makemigrations

superuser:  ## Create a Django superuser
	$(COMPOSE) run --rm api python manage.py createsuperuser

shell:  ## Django shell
	$(COMPOSE) run --rm api python manage.py shell

shell-db:  ## psql inside the internal network (no host port is published)
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-caesar} -d $${POSTGRES_DB:-caesar}

test:  ## Run the full test suite with coverage
	$(DEV) run --rm --build api pytest --cov=apps --cov-report=term-missing

test-fast:  ## Run tests, stop at the first failure
	$(DEV) run --rm --build api pytest -x -q

lint:  ## Ruff check
	$(DEV) run --rm --build api ruff check .

format:  ## Ruff format + autofix
	$(DEV) run --rm --build api sh -c "ruff format . && ruff check --fix ."

typecheck:  ## mypy
	$(DEV) run --rm --build api mypy apps config

check: lint typecheck test  ## Everything CI runs

schema:  ## Regenerate the OpenAPI schema
	$(DEV) run --rm --build api python manage.py spectacular --file schema.yml

seed:  ## Load demo data (Phase 2+)
	$(COMPOSE) run --rm api python manage.py seed_demo

clean:  ## Remove containers and volumes — DESTROYS LOCAL DATA
	$(COMPOSE) down -v

# ── Production ───────────────────────────────────────────────────────────────
# Read docs/15-dokploy.md before the first deploy. It is short.
#
# There is no separate PROD compose command any more. Production is this same
# file with `DJANGO_ENV=prod` in `.env` — which is why `make prod-check` below is
# worth running locally: it brings up the real production shape on your machine.

.PHONY: prod-config prod-check prod-logs prod-migrate backup backup-list backup-verify

prod-config:  ## Validate the compose file in both modes, the ports, and the Caddyfile (CI runs this)
	DJANGO_ENV=prod $(COMPOSE) config --quiet && echo "compose (prod) OK"
	DJANGO_ENV=dev $(COMPOSE) --profile dev config --quiet && echo "compose (dev) OK"
	python scripts/check_compose_ports.py
	docker run --rm -v "$(CURDIR)/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" 		caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile

prod-check:  ## Run the PRODUCTION shape locally — gunicorn, prod settings, real headers
	DJANGO_ENV=prod $(COMPOSE) up -d --build
	@echo "→ http://127.0.0.1:$${HTTP_PORT:-8080}   (same stack Dokploy runs)"

prod-logs:  ## Tail API logs
	$(COMPOSE) logs -f api

prod-migrate:  ## Apply migrations — take a backup first
	$(COMPOSE) exec api python manage.py backup_database --label pre-migrate
	$(COMPOSE) exec api python manage.py migrate

backup:  ## Take a backup now
	$(COMPOSE) exec api python manage.py backup_database

backup-list:  ## Show recent backups and how long since the last success
	$(COMPOSE) exec api python manage.py backup_database --list

backup-verify:  ## Re-digest the most recent backup
	$(COMPOSE) exec api python manage.py backup_database --verify

# ── Desktop client ───────────────────────────────────────────────────────────
.PHONY: vendor vendor-check desktop-build desktop-test desktop-lint signing-key

vendor:  ## Copy shared logic modules from backend into the desktop client
	python scripts/vendor_shared.py

vendor-check:  ## Fail if the vendored copies have drifted (CI runs this)
	python scripts/vendor_shared.py --check

desktop-build:  ## Build the desktop test image
	docker build -f desktop/Dockerfile.test -t caesar-desktop-test desktop

desktop-test: desktop-build  ## Run the desktop suite headless
	docker run --rm -v "$(CURDIR):/repo" -w /repo/desktop caesar-desktop-test pytest -q

desktop-lint: desktop-build  ## Lint the desktop client
	docker run --rm -v "$(CURDIR):/repo" -w /repo/desktop caesar-desktop-test ruff check .

signing-key:  ## Generate the Ed25519 keypair for offline licence tokens
	$(COMPOSE) run --rm api python manage.py generate_signing_key
