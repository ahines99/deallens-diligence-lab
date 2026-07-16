"""G37 — CI performance smoke: p95 wall-clock latency of the hot endpoints vs committed budgets.

Runs fully offline through the in-process TestClient (mock LLM, deterministic DB, no SEC network),
so it is the always-on CI gate — no k6/Locust binary required. The heavier concurrent load test is
the committed k6 script (`perf/k6_load_test.js`); both reference the same budget file
(`perf/perf_budget.json`).

Anti-flakiness: budgets carry large headroom over observed local p95 (single-digit ms on these
paths), so the assertion catches catastrophic regressions (e.g. an accidental N+1 or a lost index)
without flaking on a slow shared runner. Re-measure with `PERF_SMOKE_VERBOSE=1 ... -s` before
tightening any budget.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest

from tests.perf_seed import (
    QA_QUESTION,
    SAMPLE_ASSUMPTIONS,
    SEARCH_QUERY,
    build_perf_workspace,
)

_BUDGET_PATH = Path(__file__).resolve().parent.parent / "perf" / "perf_budget.json"


def _load_budget() -> dict:
    with _BUDGET_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


_BUDGET = _load_budget()
_ITERATIONS = int(_BUDGET.get("iterations", 20))
# Endpoint keys drive the parametrized smoke; keeping them at module scope lets pytest name cases.
_ENDPOINT_KEYS = list(_BUDGET["endpoints"].keys())

# The hot set the smoke must cover; a guard test asserts the budget file lists exactly these.
_EXPECTED_ENDPOINTS = {
    "GET /api/workspaces",
    "GET /api/workspaces/{id}",
    "POST /api/workspaces/{id}/qa",
    "GET /api/workspaces/{id}/search",
    "POST /api/workspaces/{id}/underwriting/calculate",
}


def _p95(samples_ms: list[float]) -> float:
    """Nearest-rank p95 (deterministic, no interpolation) over the measured latencies."""
    ordered = sorted(samples_ms)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _request(client, endpoint_key: str, workspace_id: str):
    if endpoint_key == "GET /api/workspaces":
        return client.get("/api/workspaces")
    if endpoint_key == "GET /api/workspaces/{id}":
        return client.get(f"/api/workspaces/{workspace_id}")
    if endpoint_key == "POST /api/workspaces/{id}/qa":
        return client.post(
            f"/api/workspaces/{workspace_id}/qa", json={"question": QA_QUESTION}
        )
    if endpoint_key == "GET /api/workspaces/{id}/search":
        return client.get(
            f"/api/workspaces/{workspace_id}/search", params={"q": SEARCH_QUERY}
        )
    if endpoint_key == "POST /api/workspaces/{id}/underwriting/calculate":
        return client.post(
            f"/api/workspaces/{workspace_id}/underwriting/calculate",
            json={"assumptions": SAMPLE_ASSUMPTIONS},
        )
    raise AssertionError(f"unmapped hot endpoint: {endpoint_key}")


@pytest.fixture(scope="module")
def perf_workspace(client) -> str:
    return build_perf_workspace(client)


def test_perf_budget_is_wellformed_and_covers_the_hot_endpoints():
    assert set(_BUDGET["endpoints"]) == _EXPECTED_ENDPOINTS
    assert _ITERATIONS >= 10, "need enough iterations for a meaningful p95"
    for key, spec in _BUDGET["endpoints"].items():
        assert isinstance(spec.get("p95_ms"), (int, float)), key
        assert spec["p95_ms"] > 0, key


@pytest.mark.parametrize("endpoint_key", _ENDPOINT_KEYS)
def test_hot_endpoint_p95_within_budget(client, perf_workspace, endpoint_key):
    budget_ms = float(_BUDGET["endpoints"][endpoint_key]["p95_ms"])

    # Warm up once (import/JIT/first-connection cost) so it doesn't skew the sample.
    warm = _request(client, endpoint_key, perf_workspace)
    assert warm.status_code == 200, warm.text

    samples_ms: list[float] = []
    for _ in range(_ITERATIONS):
        start = time.perf_counter()
        resp = _request(client, endpoint_key, perf_workspace)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert resp.status_code == 200, resp.text
        samples_ms.append(elapsed_ms)

    p95 = _p95(samples_ms)
    if os.getenv("PERF_SMOKE_VERBOSE"):
        print(
            f"\n{endpoint_key:52s} n={_ITERATIONS} "
            f"min={min(samples_ms):7.2f} med={sorted(samples_ms)[len(samples_ms)//2]:7.2f} "
            f"p95={p95:7.2f} max={max(samples_ms):7.2f} ms (budget {budget_ms:.0f} ms)"
        )
    assert p95 <= budget_ms, (
        f"{endpoint_key}: measured p95 {p95:.2f} ms exceeded budget {budget_ms:.0f} ms "
        f"(samples ms: {[round(s, 2) for s in sorted(samples_ms)]})"
    )
