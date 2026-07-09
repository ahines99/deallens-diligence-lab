# DealLens API

FastAPI backend for DealLens Diligence Lab. Runs fully in **mock mode** with SQLite — no API key, no
database server. See the root [`README.md`](../../README.md) and [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md).

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1  |  Unix: source .venv/bin/activate
pip install -e ".[dev]"
python -m src.seed.load_seed     # seed the ChainAssure demo workspace (optional; AUTO_SEED also does this)
uvicorn src.main:app --reload    # http://localhost:8000/docs
pytest                           # run the test suite
```

Key env vars (see root `.env.example`): `LLM_MODE` (default `mock`), `DATABASE_URL`
(default SQLite), `AUTO_SEED` (default true), `CORS_ORIGINS`.
