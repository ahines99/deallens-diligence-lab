# DealLens Diligence Lab — developer shortcuts.
# On Windows without `make`, run the underlying commands directly (see README).

.PHONY: help install install-api install-web seed dev api web test lint build up down clean

help:
	@echo "DealLens Diligence Lab"
	@echo "  make install      Install api + web dependencies"
	@echo "  make seed         Load the ChainAssure demo workspace into the DB"
	@echo "  make api          Run the FastAPI backend (uvicorn, reload)"
	@echo "  make web          Run the Next.js frontend (dev)"
	@echo "  make test         Run the backend pytest suite"
	@echo "  make up / down    docker compose up --build / down"

install: install-api install-web

install-api:
	cd apps/api && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

install-web:
	cd apps/web && npm install

seed:
	cd apps/api && python -m src.seed.load_seed

api:
	cd apps/api && uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && npm run dev

test:
	cd apps/api && pytest -q

lint:
	cd apps/api && python -m ruff check . || true

build:
	cd apps/web && npm run build

up:
	docker compose up --build

down:
	docker compose down

clean:
	rm -rf apps/api/data/*.sqlite3 apps/api/.pytest_cache apps/web/.next
