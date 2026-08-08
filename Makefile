COMPOSE := docker compose -f docker-compose.dev.yml

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
	$(COMPOSE) run --rm api pytest --cov=apps --cov-report=term-missing

test-fast:  ## Run tests, stop at the first failure
	$(COMPOSE) run --rm api pytest -x -q

lint:  ## Ruff check
	$(COMPOSE) run --rm api ruff check .

format:  ## Ruff format + autofix
	$(COMPOSE) run --rm api sh -c "ruff format . && ruff check --fix ."

typecheck:  ## mypy
	$(COMPOSE) run --rm api mypy apps config

check: lint typecheck test  ## Everything CI runs

schema:  ## Regenerate the OpenAPI schema
	$(COMPOSE) run --rm api python manage.py spectacular --file schema.yml

seed:  ## Load demo data (Phase 2+)
	$(COMPOSE) run --rm api python manage.py seed_demo

clean:  ## Remove containers and volumes — DESTROYS LOCAL DATA
	$(COMPOSE) down -v

# ── Production ───────────────────────────────────────────────────────────────
# Read docs/13-operations.md before the first deploy. It is short.
PROD := docker compose -f docker-compose.prod.yml

.PHONY: prod-config prod-up prod-down prod-logs prod-migrate backup backup-list backup-verify

prod-config:  ## Validate the production compose file and Caddyfile (CI runs this)
	$(PROD) config --quiet && echo "compose OK"
	docker run --rm -v "$(CURDIR)/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
		-e DOMAIN -e ACME_EMAIL caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile

prod-up:  ## Start the production stack
	$(PROD) up -d

prod-down:  ## Stop the production stack (data volumes survive)
	$(PROD) down

prod-logs:  ## Tail production API logs
	$(PROD) logs -f api

prod-migrate:  ## Apply migrations in production — take a backup first
	$(PROD) exec api python manage.py backup_database --label pre-migrate
	$(PROD) exec api python manage.py migrate

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
