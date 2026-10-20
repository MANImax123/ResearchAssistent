.PHONY: up down logs ps migrate test lint worker seed

# Load .env if it exists
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

migrate:
	docker compose exec backend alembic upgrade head

test:
	docker compose exec backend pytest -v

lint:
	docker compose exec backend ruff check app/
	docker compose exec backend mypy app/

worker:
	docker compose exec worker celery -A app.workers.celery_app worker --loglevel=info

seed:
	docker compose exec backend python -m app.db.seed
