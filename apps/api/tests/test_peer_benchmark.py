"""G64 — XBRL frames peer benchmarking (offline, synthetic frames; live tests auto-skip).

The load-bearing disciplines: hand-verified midrank percentile math (including ties and a target
below the entire universe), the both-frames-required join for margins, explicit coverage counts
with an "insufficient peer coverage" floor (a thin frame never fabricates a percentile), and
outage honesty (EDGAR down => ``unavailable`` with a ``source_error``, never a clean empty).
Offline tests monkeypatch the EDGAR client exactly like tests/test_ownership.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services import edgar_client, peer_benchmark_service
from src.services.peer_benchmark_service import (
    COVERAGE_FLOOR,
    frame_values_by_cik,
    growth_universe,
    margin_universe,
    percentile_rank,
)

REV_CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
TARGET_CIK = 999


# --- (a) percentile math, hand-verified --------------------------------------
def test_percentile_rank_midrank_hand_cases():
    # below=2, ties=0, n=4 -> 0.5
    assert percentile_rank([10.0, 20.0, 30.0, 40.0], 25.0) == 0.5
    # exact member: below=1, ties=1, n=4 -> (1 + 0.5)/4 = 0.375
    assert percentile_rank([10.0, 20.0, 30.0, 40.0], 20.0) == 0.375
    # ties: [1, 2, 2, 3] target 2 -> (1 + 0.5*2)/4 = 0.5
    assert percentile_rank([1.0, 2.0, 2.0, 3.0], 2.0) == 0.5
    # every value ties the target -> exactly the median rank
    assert percentile_rank([5.0, 5.0, 5.0], 5.0) == 0.5
    # target below the entire universe -> 0.0 (a real rank, not a missing one)
    assert percentile_rank([5.0, 6.0, 7.0], 1.0) == 0.0
    # target above the entire universe -> 1.0
    assert percentile_rank([5.0, 6.0, 7.0], 9.0) == 1.0
    # empty universe -> None (a percentile against nobody is absent, not zero)
    assert percentile_rank([], 1.0) is None


def test_growth_universe_requires_both_years_and_positive_prior():
    current = {1: 110.0, 2: 240.0, 3: 300.0, 4: 100.0}
    prior = {1: 100.0, 2: 200.0, 4: 0.0, 5: 50.0}
    # cik 3 lacks a prior year, cik 5 lacks a current year, cik 4 has a zero prior — all excluded.
    assert sorted(growth_universe(current, prior)) == [pytest.approx(0.1), pytest.approx(0.2)]


def test_margin_universe_requires_membership_in_both_frames():
    numerator = {1: 10.0, 2: 20.0, 777: 999.0}
    revenue = {1: 100.0, 2: 200.0, 3: 300.0}
    # 777 reports operating income but no revenue; 3 reports revenue but no operating income.
    assert sorted(margin_universe(numerator, revenue)) == [
        pytest.approx(0.1),
        pytest.approx(0.1),
    ]


def test_frame_values_by_cik_drops_rows_without_numeric_values():
    frame = {
        "data": [
            {"cik": 1, "entityName": "A", "val": 10.0},
            {"cik": 2, "entityName": "B", "val": None},
            {"cik": None, "entityName": "C", "val": 5.0},
            {"cik": 3, "entityName": "D", "val": "not-a-number"},
        ]
    }
    assert frame_values_by_cik(frame) == {1: 10.0}


# --- fixture plumbing --------------------------------------------------------
def _frame(entries: list[tuple[int, float]]) -> dict:
    return {
        "data": [
            {"cik": cik, "entityName": f"Entity {cik}", "val": val, "accn": f"acc-{cik}"}
            for cik, val in entries
        ]
    }


def _install_target(monkeypatch, **overrides):
    target = SimpleNamespace(
        cik="0000000999",
        name="Bench Co",
        sector="Services-Prepackaged Software",
        revenue_growth=0.125,
        operating_margin=0.25,
        financials={"trends": {"years": ["2024", "2025"]}},
    )
    for key, value in overrides.items():
        setattr(target, key, value)
    monkeypatch.setattr(peer_benchmark_service, "get_target", lambda s, w: target)
    return target


def _install_frames(monkeypatch, frames: dict[tuple[str, int], dict]):
    def fake_frames(concept: str, year: int, unit: str = "USD") -> dict:
        key = (concept, int(year))
        if key not in frames:
            # A frame the SEC has not published surfaces as an HTTP error => EdgarError.
            raise edgar_client.EdgarError(f"no frame for {concept} CY{year}")
        return frames[key]

    monkeypatch.setattr(edgar_client, "frames_annual", fake_frames)


def _install_submissions(monkeypatch, payload=None):
    payload = payload or {"sic": "7372", "sicDescription": "Services-Prepackaged Software"}
    monkeypatch.setattr(edgar_client, "get_submissions", lambda *_a, **_k: payload)


# Universe design (hand-computed):
#   Revenue CY2024: ciks 1..24 all report 100.0 (plus the target itself at 100.0).
#   Revenue CY2025: cik i reports 100 + i (plus the target at 200.0 — growth 1.0 if wrongly
#     included). Peer growths are therefore i/100 for i = 1..24: 0.01, 0.02, ..., 0.24.
#     Target growth 0.125 -> below = 12 (0.01..0.12), ties = 0, n = 24 -> percentile 0.5.
#     (If the target's own row leaked into its universe: (12 + 0)/25 = 0.48 — distinguishable.)
#   OperatingIncomeLoss CY2025: ciks 1..10 at margin 0.25, ciks 11..22 at margin 0.10; cik 777
#     reports operating income but NO revenue (must be excluded by the join); ciks 23/24 report
#     revenue but no operating income. Margin universe = 22 values: ten 0.25s, twelve 0.10s.
#     Target margin 0.25 -> below = 12, ties = 10 -> (12 + 5)/22 = 0.7727.
def _standard_frames() -> dict[tuple[str, int], dict]:
    rev_2025 = [(cik, 100.0 + cik) for cik in range(1, 25)] + [(TARGET_CIK, 200.0)]
    rev_2024 = [(cik, 100.0) for cik in range(1, 25)] + [(TARGET_CIK, 100.0)]
    oi_2025 = (
        [(cik, 0.25 * (100.0 + cik)) for cik in range(1, 11)]
        + [(cik, 0.10 * (100.0 + cik)) for cik in range(11, 23)]
        + [(777, 999.0), (TARGET_CIK, 50.0)]
    )
    return {
        (REV_CONCEPT, 2025): _frame(rev_2025),
        (REV_CONCEPT, 2024): _frame(rev_2024),
        ("OperatingIncomeLoss", 2025): _frame(oi_2025),
    }


# --- (b) end-to-end percentiles on fixture frames ----------------------------
def test_build_percentiles_hand_verified_and_target_excluded(monkeypatch):
    _install_target(monkeypatch)
    _install_submissions(monkeypatch)
    _install_frames(monkeypatch, _standard_frames())

    out = peer_benchmark_service.build(object(), "ws1")
    assert out["status"] == "available"
    assert out["as_of_year"] == 2025
    assert out["target_sic"] == "7372"
    assert out["sic_description"] == "Services-Prepackaged Software"
    assert REV_CONCEPT in out["peer_scope"] and "CY2025" in out["peer_scope"]
    assert "not SIC-restricted" in out["peer_scope"]

    growth = next(m for m in out["metrics"] if m["metric"] == "revenue_growth")
    # 24 peers — the target's own frame row is excluded (25 would shift the percentile to 0.48).
    assert growth["coverage"] == 24
    assert growth["target_value"] == 0.125
    assert growth["percentile"] == 0.5
    assert growth["concepts"] == [REV_CONCEPT]

    margin = next(m for m in out["metrics"] if m["metric"] == "operating_margin")
    # 22 joined entities: cik 777 (operating income only) and ciks 23/24 (revenue only) are out.
    assert margin["coverage"] == 22
    assert margin["percentile"] == pytest.approx(round(17 / 22, 4))
    assert margin["concepts"] == ["OperatingIncomeLoss", REV_CONCEPT]
    assert out["source_error"] is None


def test_target_below_entire_universe_reports_zero_not_missing(monkeypatch):
    _install_target(monkeypatch, revenue_growth=-0.5)
    _install_submissions(monkeypatch)
    _install_frames(monkeypatch, _standard_frames())

    out = peer_benchmark_service.build(object(), "ws1")
    growth = next(m for m in out["metrics"] if m["metric"] == "revenue_growth")
    # 0.0 is a real rank (bottom of the universe) and must not be swallowed as falsy/absent.
    assert growth["percentile"] == 0.0
    assert out["status"] == "available"


# --- (c) coverage-floor honesty ----------------------------------------------
def test_thin_frames_degrade_to_insufficient_peer_coverage(monkeypatch):
    _install_target(monkeypatch)
    _install_submissions(monkeypatch)
    thin = {
        (REV_CONCEPT, 2025): _frame([(cik, 110.0) for cik in range(1, 6)]),
        (REV_CONCEPT, 2024): _frame([(cik, 100.0) for cik in range(1, 6)]),
        ("OperatingIncomeLoss", 2025): _frame([(cik, 11.0) for cik in range(1, 6)]),
    }
    _install_frames(monkeypatch, thin)

    out = peer_benchmark_service.build(object(), "ws1")
    assert out["status"] == "partial"  # honest, not a fabricated percentile from 5 peers
    for metric in out["metrics"]:
        assert metric["percentile"] is None
        assert metric["coverage"] == 5  # the thin count is still reported
        assert "insufficient peer coverage" in metric["note"]
        assert str(COVERAGE_FLOOR) in metric["note"]


# --- (d) outage honesty: unavailable, never a clean empty --------------------
def test_frames_outage_is_unavailable_not_clean_empty(monkeypatch):
    _install_target(monkeypatch)
    _install_submissions(monkeypatch)
    _install_frames(monkeypatch, {})  # every frame fetch raises EdgarError

    out = peer_benchmark_service.build(object(), "ws1")
    assert out["status"] == "unavailable"
    assert out["metrics"] == []
    assert out["source_error"]


def test_submissions_outage_is_unavailable(monkeypatch):
    _install_target(monkeypatch)
    monkeypatch.setattr(
        edgar_client,
        "get_submissions",
        lambda *_a, **_k: (_ for _ in ()).throw(edgar_client.EdgarError("offline")),
    )
    out = peer_benchmark_service.build(object(), "ws1")
    assert out["status"] == "unavailable"
    assert out["source_error"]


def test_operating_income_frame_outage_degrades_only_that_metric(monkeypatch):
    _install_target(monkeypatch)
    _install_submissions(monkeypatch)
    frames = _standard_frames()
    del frames[("OperatingIncomeLoss", 2025)]
    _install_frames(monkeypatch, frames)

    out = peer_benchmark_service.build(object(), "ws1")
    assert out["status"] == "partial"
    growth = next(m for m in out["metrics"] if m["metric"] == "revenue_growth")
    assert growth["percentile"] == 0.5  # unaffected
    margin = next(m for m in out["metrics"] if m["metric"] == "operating_margin")
    assert margin["percentile"] is None
    assert margin["coverage"] == 0
    assert "unavailable" in margin["note"]
    assert out["source_error"]


# --- (e) concept fallback + missing anchors ----------------------------------
def test_revenue_concept_falls_back_without_mixing_concepts(monkeypatch):
    _install_target(monkeypatch)
    _install_submissions(monkeypatch)
    # The first Revenues-family concept has no frames at all (EdgarError, as a 404 surfaces);
    # "Revenues" carries both years and must be chosen for BOTH — never one year per concept.
    frames = {
        ("Revenues", 2025): _frame([(cik, 110.0) for cik in range(1, 26)]),
        ("Revenues", 2024): _frame([(cik, 100.0) for cik in range(1, 26)]),
        ("OperatingIncomeLoss", 2025): _frame([(cik, 22.0) for cik in range(1, 26)]),
    }
    _install_frames(monkeypatch, frames)

    out = peer_benchmark_service.build(object(), "ws1")
    growth = next(m for m in out["metrics"] if m["metric"] == "revenue_growth")
    assert growth["concepts"] == ["Revenues"]
    assert growth["coverage"] == 25
    assert "Revenues" in out["peer_scope"]


def test_missing_trend_years_is_unavailable_with_refresh_note(monkeypatch):
    _install_target(monkeypatch, financials=None)
    out = peer_benchmark_service.build(object(), "ws1")
    assert out["status"] == "unavailable"
    assert "refresh" in out["source_error"]


# --- (f) response-model contract ---------------------------------------------
def test_service_payload_round_trips_through_the_response_model(monkeypatch):
    from src.routers.peer_benchmark import router  # the route module imports cleanly
    from src.schemas.peer_benchmark import PeerBenchmark

    assert any(r.path.endswith("/peer-benchmark") for r in router.routes)
    _install_target(monkeypatch)
    _install_submissions(monkeypatch)
    _install_frames(monkeypatch, _standard_frames())
    model = PeerBenchmark.model_validate(peer_benchmark_service.build(object(), "ws1"))
    assert model.status == "available"
    assert {m.metric for m in model.metrics} == {"revenue_growth", "operating_margin"}


# --- (g) live checks (auto-skip offline, mirroring test_phase0_truth) --------
def test_live_frames_universe_shape(sec_online):
    if not sec_online:
        pytest.skip("SEC EDGAR unreachable")
    try:
        frame = edgar_client.frames_annual("Revenues", 2023)
    except edgar_client.EdgarError as exc:
        pytest.skip(f"SEC XBRL frames unavailable: {exc}")
    rows = frame.get("data", [])
    assert len(rows) > 500  # the whole reporting universe, not a handful
    assert {"cik", "entityName", "val"} <= set(rows[0])


def test_live_peer_benchmark_build(client, live_workspace_id):
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        out = peer_benchmark_service.build(session, live_workspace_id)
    if out["status"] == "unavailable":
        pytest.skip(f"frames unavailable live: {out['source_error']}")
    assert out["status"] in {"available", "partial"}
    assert isinstance(out["as_of_year"], int)
    assert out["metrics"]
    for metric in out["metrics"]:
        if metric["percentile"] is not None:
            assert 0.0 <= metric["percentile"] <= 1.0
            assert metric["coverage"] >= COVERAGE_FLOOR
