# DealLens Diligence Lab — developer shortcuts.
# On Windows without `make`, run the underlying commands directly (see README).

.PHONY: help install install-api install-web seed dev api web test test-api test-web lint build up down clean

help:
	@echo "DealLens Diligence Lab"
	@echo "  make install      Install api + web dependencies"
	@echo "  make seed         Load live-SEC demo workspaces (MSFT, CRWD) into the DB"
	@echo "  make api          Run the FastAPI backend (uvicorn, reload)"
	@echo "  make web          Run the Next.js frontend (dev)"
	@echo "  make dev          Run API + web development servers"
	@echo "  make test         Run backend and frontend test suites"
	@echo "  make up / down    docker compose up --build / down"

install: install-api install-web

install-api:
	cd apps/api && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

install-web:
	cd apps/web && npm install

seed:
	cd apps/api && . .venv/bin/activate && python -m src.seed.load_seed

api:
	cd apps/api && . .venv/bin/activate && uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && npm run dev

dev:
	$(MAKE) -j2 api web

test: test-api test-web

test-api:
	cd apps/api && . .venv/bin/activate && python -m pytest -q

test-web:
	cd apps/web && npm test

lint:
	cd apps/api && . .venv/bin/activate && python -m ruff check src tests migrations
	cd apps/web && npm run lint

build:
	cd apps/web && npm run build

up:
	docker compose up --build

down:
	docker compose down

clean:
	rm -rf apps/api/data/*.sqlite3 apps/api/.pytest_cache apps/web/.next
