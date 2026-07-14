# DealLens API

FastAPI backend for DealLens Diligence Lab. Runs fully in **mock mode** with SQLite — no API key, no
database server. See the root [`README.md`](../../README.md) and [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md).

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1  |  Unix: source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head             # apply database migrations
uvicorn src.main:app --reload    # http://localhost:8000/docs
pytest                           # run the test suite (live SEC tests skip when offline)
```

No seeding is required: workspaces are created interactively (any public ticker builds live from
SEC EDGAR; `POST /api/examples/private-deal` loads the fictional example private deal).
`python -m src.seed.load_seed` remains a dev utility that pre-ingests MSFT/CRWD.

Key env vars (see root `.env.example`): `SEC_USER_AGENT` (required for live SEC ingest),
`LLM_MODE` (default `mock`), `DATABASE_URL` (default SQLite), `AUTH_REQUIRED` (default true),
`AUTO_SEED` (default false; set `AUTH_REQUIRED=false` or `SEED_ORGANIZATION_SLUG` when seeding),
`CORS_ORIGINS`.
