# Load testing & performance budgets (G37)

Two complementary layers guard the latency of the hot API endpoints:

| Layer | Tool | Runs where | What it does |
|---|---|---|---|
| **CI perf smoke** (the gate) | pytest + in-process `TestClient` | Every CI run, no external tooling | Exercises each hot endpoint 20× offline, computes wall-clock p95, asserts p95 ≤ budget |
| **Load test** (heavier, out-of-CI) | [k6](https://k6.io) | On demand, against a running server | Concurrent virtual users with staged ramp-up; fails if p95 breaches the budget under load |

The single source of truth for budgets is [`perf_budget.json`](./perf_budget.json):
`{ endpoints: { "<METHOD> <template>": { p95_ms, ... } } }`. Both layers reference it (the k6
thresholds are kept in sync by hand — update both when a budget changes).

## Hot endpoints covered

- `GET  /api/workspaces` — workspace list
- `GET  /api/workspaces/{id}` — workspace overview
- `POST /api/workspaces/{id}/qa` — cited extractive Q&A (BM25 retrieval, mock LLM, no network)
- `GET  /api/workspaces/{id}/search` — full-text artifact search
- `POST /api/workspaces/{id}/underwriting/calculate` — LBO underwriting compute

All run fully offline on the deterministic/mock paths (no live SEC/EDGAR, no live LLM).

## Running the CI perf smoke locally

```bash
cd apps/api
python -m pytest tests/test_perf_smoke.py -q
```

It is a normal pytest module, so the ordinary `python -m pytest` run and CI's `Test` step already
collect it; CI additionally runs it as a labeled **"Performance smoke"** step for a standalone
signal. Budgets carry large headroom over observed local p95 (single-digit ms) precisely so this
never flakes on a slow shared runner — it is a catastrophic-regression guard, not a micro-benchmark.

## Running the k6 load test (out of CI)

k6 is a standalone binary, **not** a Python dependency — install it from
<https://k6.io/docs/get-started/installation/>.

1. Start the API against a throwaway SQLite DB, mock LLM, no auth:

   ```bash
   cd apps/api
   LLM_MODE=mock AUTO_SEED=false AUTH_REQUIRED=false \
     DATABASE_URL=sqlite:///./perf.sqlite3 SCHEMA_MANAGEMENT=create_all \
     uvicorn src.main:app --port 8000
   ```

2. Seed one workspace with fixture filings/chunks (mirrors the smoke fixture) and print its id:

   ```bash
   LLM_MODE=mock AUTO_SEED=false AUTH_REQUIRED=false \
     DATABASE_URL=sqlite:///./perf.sqlite3 SCHEMA_MANAGEMENT=create_all \
     python -m tests.perf_seed   # prints WORKSPACE_ID=...
   ```

   (or create a private workspace via `POST /api/workspaces` and add a filing + chunks; the
   `list` endpoint alone can be load-tested without a workspace id.)

3. Run k6 against the live server:

   ```bash
   k6 run -e BASE_URL=http://localhost:8000 -e WORKSPACE_ID=<id> perf/k6_load_test.js
   # add -e TOKEN=dls_... if you started the server with AUTH_REQUIRED=true
   ```

k6 prints per-endpoint p95 and exits non-zero if any threshold (mirroring `perf_budget.json`) is
breached.

## Re-measuring before changing budgets

The smoke test can print observed latencies for calibration:

```bash
cd apps/api
PERF_SMOKE_VERBOSE=1 python -m pytest tests/test_perf_smoke.py -q -s
```

Set new `p95_ms` values to roughly 3–5× the observed p95 (or looser) so the gate stays stable.
