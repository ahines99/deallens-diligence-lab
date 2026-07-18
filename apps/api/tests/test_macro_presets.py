"""G68 — macro-linked Monte Carlo presets: transparent v1 mapping over fixture FRED payloads.

The mapping under test is a CONFIG with reviewable constants, hand-verified here:
* DGS10 [4.0, 4.2, 4.4] -> base_rate_shift ~ normal(4.4/100 = 0.044, stdev/100 = 0.2/100 = 0.002)
* BAA10Y [1.5, 2.0, 2.5] -> exit_multiple ~ normal(10.0 + (-1.0) x (2.5 - 2.0) = 9.5, stdev = 0.5)
* GDP [1.0, 2.0, 3.0]    -> revenue_growth_shift ~ normal(1.0 x (3.0 - 2.0)/100 = 0.01, 1.0/100 = 0.01)

An unavailable series must yield NO distribution (omitted + noted, never fabricated), and every
emitted dict must validate against the untouched MonteCarloRequest.distributions schema.
"""
from __future__ import annotations

import pytest

from src.schemas.underwriting_model import DriverDistribution, MonteCarloRequest
from src.services import fred_service, macro_preset_service


def _summary(series_id: str, values: list[float]) -> dict:
    points = [{"date": f"2025-06-{i + 1:02d}", "value": v} for i, v in enumerate(values)]
    return {
        "series_id": series_id,
        "label": series_id,
        "unit": "pct",
        "note": "",
        "latest_value": values[-1],
        "latest_date": points[-1]["date"],
        "yoy_change": None,
        "points": points,
    }


_FIXTURES = {
    "DGS10": [4.0, 4.2, 4.4],
    "BAA10Y": [1.5, 2.0, 2.5],
    "A191RL1Q225SBEA": [1.0, 2.0, 3.0],
}


def _patch_fred(monkeypatch, available: dict[str, list[float]]):
    def fake(series_id: str):
        values = available.get(series_id)
        return _summary(series_id, values) if values is not None else None

    monkeypatch.setattr(fred_service, "_fetch_series", fake)


def test_mapping_math_hand_verified(monkeypatch):
    _patch_fred(monkeypatch, _FIXTURES)
    out = macro_preset_service.build()
    assert out["status"] == "available"
    assert out["preset_version"] == macro_preset_service.PRESET_VERSION == "v1"
    by_driver = {d["driver"]: d for d in out["distributions"]}
    assert set(by_driver) == {"base_rate_shift", "exit_multiple", "revenue_growth_shift"}

    # DGS10: mean = 4.4/100; std = stdev([4.0, 4.2, 4.4])/100 = 0.2/100 (sample std, n-1).
    base = by_driver["base_rate_shift"]
    assert base["kind"] == "normal"
    assert base["mean"] == pytest.approx(0.044)
    assert base["std_dev"] == pytest.approx(0.002)

    # BAA10Y: mean = 10.0 + (-1.0) x (2.5 - 2.0) = 9.5; std = |-1.0| x stdev = 0.5.
    exit_mult = by_driver["exit_multiple"]
    assert exit_mult["mean"] == pytest.approx(9.5)
    assert exit_mult["std_dev"] == pytest.approx(0.5)

    # GDP: mean = 1.0 x (3.0 - 2.0)/100 = 0.01; std = 1.0 x stdev([1,2,3])/100 = 0.01.
    growth = by_driver["revenue_growth_shift"]
    assert growth["mean"] == pytest.approx(0.01)
    assert growth["std_dev"] == pytest.approx(0.01)

    # Wider spread -> LOWER mean exit multiple (the documented direction of the linear map).
    _patch_fred(monkeypatch, {**_FIXTURES, "BAA10Y": [3.0, 3.5, 4.0]})
    wider = macro_preset_service.build()
    wide_mult = next(d for d in wider["distributions"] if d["driver"] == "exit_multiple")
    assert wide_mult["mean"] == pytest.approx(8.0)  # 10.0 - 1.0 x (4.0 - 2.0)
    assert wide_mult["mean"] < exit_mult["mean"]


def test_distributions_validate_against_mc_schema(monkeypatch):
    _patch_fred(monkeypatch, _FIXTURES)
    out = macro_preset_service.build()
    for entry in out["distributions"]:
        dist = DriverDistribution.model_validate(entry)  # provenance is ignored by the schema
        assert dist.kind == "normal"
        assert dist.std_dev is not None and dist.std_dev >= 0
    # And the full list is paste-ready as MonteCarloRequest.distributions (engine untouched).
    request = MonteCarloRequest.model_validate(
        {
            "assumptions": {
                "historical": {"ltm_revenue": 100.0, "ltm_ebitda": 20.0},
                "transaction": {
                    "close_date": "2026-01-01",
                    "entry_multiple": 10.0,
                    "exit_multiple": 10.0,
                },
            },
            "distributions": out["distributions"],
        }
    )
    assert len(request.distributions) == 3


def test_provenance_rides_on_each_distribution(monkeypatch):
    _patch_fred(monkeypatch, _FIXTURES)
    out = macro_preset_service.build()
    for entry in out["distributions"]:
        prov = entry["provenance"]
        assert prov["preset_version"] == "v1"
        assert prov["series_id"] in _FIXTURES
        assert prov["as_of"]
        assert "normal(" in prov["mapping"]  # the linear map is spelled out, not hidden
    # The client-side-provenance design decision is stated in the mapping notes.
    assert any("client-side" in note for note in out["mapping_notes"])


def test_per_series_outage_omits_entry_never_fabricates(monkeypatch):
    _patch_fred(monkeypatch, {k: v for k, v in _FIXTURES.items() if k != "BAA10Y"})
    out = macro_preset_service.build()
    assert out["status"] == "partial"
    drivers = {d["driver"] for d in out["distributions"]}
    assert drivers == {"base_rate_shift", "revenue_growth_shift"}  # exit_multiple absent
    spread_row = next(row for row in out["series"] if row["series_id"] == "BAA10Y")
    assert spread_row["last_value"] is None
    assert "never fabricated" in spread_row["note"]
    assert any("omitted" in note for note in out["mapping_notes"])


def test_all_series_unavailable_reports_unavailable(monkeypatch):
    _patch_fred(monkeypatch, {})
    out = macro_preset_service.build()
    assert out["status"] == "unavailable"
    assert out["distributions"] == []
    assert all(row["last_value"] is None for row in out["series"])


def test_thin_history_omits_entry(monkeypatch):
    _patch_fred(monkeypatch, {**_FIXTURES, "DGS10": [4.4]})  # one obs: no std computable
    out = macro_preset_service.build()
    drivers = {d["driver"] for d in out["distributions"]}
    assert "base_rate_shift" not in drivers
    row = next(row for row in out["series"] if row["series_id"] == "DGS10")
    assert "fewer than 2 observations" in row["note"]
    assert row["last_value"] == 4.4  # the observed value itself is still reported honestly


# --- endpoint contract -----------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _wire_router(client):
    """Mount the Wave 6 router (integrator wires it into main.py; no-op once that lands)."""
    from src.main import app
    from src.routers import research_wave6

    have = {getattr(r, "path", "") for r in app.routes}
    if "/api/workspaces/{workspace_id}/macro-mc-presets" not in have:
        app.include_router(research_wave6.router)
    yield


def _make_workspace() -> str:
    from src.db.session import SessionLocal
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as s:
        ws = workspace_service.create_workspace(
            s, WorkspaceCreate(name="Macro Co", deal_type="buyout")
        )
        s.commit()
        return ws.id


def test_macro_presets_endpoint_contract(client, monkeypatch):
    _patch_fred(monkeypatch, _FIXTURES)
    wid = _make_workspace()
    body = client.get(f"/api/workspaces/{wid}/macro-mc-presets").json()
    assert body["workspace_id"] == wid
    assert body["status"] == "available"
    assert body["preset_version"] == "v1"
    assert len(body["distributions"]) == 3
    assert len(body["series"]) == 3
    assert body["mapping_notes"]


def test_macro_presets_endpoint_unknown_workspace_404(client, monkeypatch):
    _patch_fred(monkeypatch, _FIXTURES)
    assert client.get("/api/workspaces/nope/macro-mc-presets").status_code == 404


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
