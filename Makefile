.DEFAULT_GOAL := help
.PHONY: help up down reset seed superuser logs test coverage test-local coverage-html lint format

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

up: ## Build and start the API (http://localhost:8000/api/docs) with MariaDB
	docker compose up --build -d web
	@echo "API docs: http://localhost:8000/api/docs"

down: ## Stop the containers (data is kept)
	docker compose --profile test down

reset: ## Stop the containers and delete all data
	docker compose --profile test down --volumes

seed: ## Load the demo catalog
	docker compose exec web python manage.py seed_catalog

superuser: ## Create a staff user (needed for every write request)
	docker compose exec web python manage.py createsuperuser

logs: ## Follow the API logs
	docker compose logs -f web

test: ## Run the test suite in Docker against MariaDB
	docker compose run --rm --build test

coverage: ## Test suite with line + branch coverage (fails below the threshold in pyproject.toml)
	docker compose run --rm --build test pytest --cov

test-local: ## Run the tests on the host (needs `uv sync` and `docker compose up -d db`)
	uv run pytest

coverage-html: ## Coverage on the host as a browsable report in htmlcov/index.html
	uv run pytest --cov --cov-report=html
	@echo "open htmlcov/index.html"

lint: ## Check style and common mistakes
	uv run ruff check .
	uv run ruff format --check .

format: ## Fix what can be fixed automatically
	uv run ruff check --fix .
	uv run ruff format .
