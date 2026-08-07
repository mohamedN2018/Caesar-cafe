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
