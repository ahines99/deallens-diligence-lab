"""G66 — buyback & dilution analysis (offline, synthetic XBRL facts; live test auto-skips).

Load-bearing disciplines: hand-computed per-year derivation (net dilution = YoY change in shares
outstanding across CONSECUTIVE tagged fiscal years), per-year missing-concept honesty (None,
never interpolated — including never across a gap year), CY-frame period keying (comparatives
restated in one 10-K share the filing's ``fy`` and must NOT collapse — the regression fixture
mirrors tests/test_phase0_truth.py), and outage honesty (EDGAR down => ``unavailable`` with a
``source_error``, never a clean empty).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services import dilution_service, edgar_client

SHARES = "CommonStockSharesOutstanding"
SBC = "ShareBasedCompensation"
REPO = "PaymentsForRepurchaseOfCommonStock"


def _facts(concepts: dict) -> dict:
    return {"facts": {"us-gaap": concepts}}


def _usd(points: list[dict]) -> dict:
    return {"units": {"USD": points}}


def _shares_unit(points: list[dict]) -> dict:
    return {"units": {"shares": points}}


def _duration(year: int, val: float, **extra) -> dict:
    return {
        "start": f"{year}-01-01",
        "end": f"{year}-12-31",
        "val": val,
        "frame": f"CY{year}",
        "form": "10-K",
        "fp": "FY",
        "accn": f"k{year}",
        "filed": f"{year + 1}-02-01",
        **extra,
    }


def _instant(year: int, val: float, **extra) -> dict:
    return {
        "end": f"{year}-12-31",
        "val": val,
        "form": "10-K",
        "fp": "FY",
        "accn": f"k{year}",
        "filed": f"{year + 1}-02-01",
        **extra,
    }


# Shares 1000 -> 950 -> 969:
#   net dilution 2023 = (950 - 1000)/1000 = -0.05 ; 2024 = (969 - 950)/950 = 0.02 exactly.
def _standard_facts() -> dict:
    return _facts(
        {
            "Revenues": _usd([_duration(2022, 500.0), _duration(2023, 550.0),
                              _duration(2024, 600.0)]),
            SHARES: _shares_unit(
                [_instant(2022, 1000.0), _instant(2023, 950.0), _instant(2024, 969.0)]
            ),
            SBC: _usd([_duration(2022, 40.0), _duration(2023, 44.0), _duration(2024, 50.0)]),
            REPO: _usd([_duration(2022, 100.0), _duration(2023, 120.0), _duration(2024, 90.0)]),
        }
    )


# --- (a) per-year derivation, hand-computed ----------------------------------
def test_per_year_derivation_hand_computed():
    out = dilution_service.build(_standard_facts())
    assert out["status"] == "available"
    assert out["years"] == ["2022", "2023", "2024"]
    assert out["by_year"]["2022"] == {
        "shares_out": 1000.0, "sbc": 40.0, "repurchases": 100.0, "net_dilution_pct": None,
    }
    assert out["by_year"]["2023"]["net_dilution_pct"] == pytest.approx(-0.05)
    assert out["by_year"]["2024"]["net_dilution_pct"] == pytest.approx(0.02)
    assert out["by_year"]["2024"]["shares_out"] == 969.0
    assert out["by_year"]["2024"]["sbc"] == 50.0
    assert out["by_year"]["2024"]["repurchases"] == 90.0
    assert out["sources"] == {"shares_out": SHARES, "sbc": SBC, "repurchases": REPO}
    # Citations bind concept + accession/form for every tagged point.
    assert out["citations"]["2024"]["shares_out"] == {
        "concept": SHARES, "end": "2024-12-31", "accession": "k2024", "form": "10-K",
    }
    assert out["citations"]["2023"]["repurchases"]["accession"] == "k2023"


# --- (b) missing-concept honesty: None per year, never interpolated ----------
def test_missing_concept_years_stay_none_and_are_reported():
    facts = _standard_facts()
    # SBC untagged for 2023 only; repurchases never tagged at all.
    facts["facts"]["us-gaap"][SBC] = _usd([_duration(2022, 40.0), _duration(2024, 50.0)])
    del facts["facts"]["us-gaap"][REPO]

    out = dilution_service.build(facts)
    assert out["status"] == "partial"
    # The 2023 SBC hole is None — NOT the midpoint 45.0 an interpolation would produce.
    assert out["by_year"]["2023"]["sbc"] is None
    assert out["by_year"]["2022"]["sbc"] == 40.0
    assert out["by_year"]["2024"]["sbc"] == 50.0
    assert all(out["by_year"][y]["repurchases"] is None for y in out["years"])
    assert out["sources"]["repurchases"] is None
    # Untouched fields keep their derivations.
    assert out["by_year"]["2023"]["net_dilution_pct"] == pytest.approx(-0.05)
    assert "never interpolated" in out["note"]
    assert "sbc (2023)" in out["note"]
    assert "repurchases (2022, 2023, 2024)" in out["note"]
    assert "sbc" not in out["citations"]["2023"]


def test_net_dilution_is_never_derived_across_a_gap_year():
    facts = _standard_facts()
    # Shares untagged for 2023: 2024's prior CONSECUTIVE year is missing.
    facts["facts"]["us-gaap"][SHARES] = _shares_unit(
        [_instant(2022, 1000.0), _instant(2024, 900.0)]
    )
    out = dilution_service.build(facts)
    assert out["status"] == "partial"
    assert out["by_year"]["2023"]["shares_out"] is None
    assert out["by_year"]["2024"]["shares_out"] == 900.0
    # NOT (900 - 1000)/1000 = -0.1 — a two-year span is not a YoY rate.
    assert out["by_year"]["2024"]["net_dilution_pct"] is None


def test_zero_prior_share_count_never_divides():
    facts = _standard_facts()
    facts["facts"]["us-gaap"][SHARES] = _shares_unit(
        [_instant(2023, 0.0), _instant(2024, 100.0)]
    )
    out = dilution_service.build(facts)
    assert out["by_year"]["2024"]["net_dilution_pct"] is None


def test_instants_without_a_fiscal_year_anchor_are_dropped_not_guessed():
    # No revenue durations => no fiscal-year balance-sheet-date map => instant share counts have
    # no year label and are dropped (never guessed onto a calendar year). Durations still key by
    # their own CY frames.
    facts = _facts(
        {
            SHARES: _shares_unit([_instant(2023, 1000.0), _instant(2024, 1010.0)]),
            SBC: _usd([_duration(2023, 44.0), _duration(2024, 50.0)]),
        }
    )
    out = dilution_service.build(facts)
    assert out["status"] == "partial"
    assert out["years"] == ["2023", "2024"]
    assert all(out["by_year"][y]["shares_out"] is None for y in out["years"])
    assert out["by_year"]["2024"]["sbc"] == 50.0


def test_no_relevant_concepts_is_unavailable_not_clean_empty():
    facts = _facts({"Revenues": _usd([_duration(2024, 600.0)])})
    out = dilution_service.build(facts)
    assert out["status"] == "unavailable"
    assert out["years"] == []
    assert out["by_year"] == {}
    assert "nothing imputed" in out["note"]


# --- (c) CY-frame keying: comparatives sharing the filing fy do not collapse -
def test_comparative_periods_sharing_the_filing_fy_do_not_collapse():
    """Live-SEC regression (mirrors test_phase0_truth): every point restated in one 10-K carries
    the REPORTING filing's ``fy``, so keying periods by ``fy`` would collapse three fiscal years
    into one. Periods must key by CY frame (durations) / balance-sheet date (instants)."""
    one_filing = {"form": "10-K", "fy": 2025, "accn": "acc-2025", "filed": "2025-11-01"}
    facts = _facts(
        {
            "Revenues": _usd(
                [
                    {"start": "2023-01-01", "end": "2023-12-31", "val": 500.0,
                     "frame": "CY2023", **one_filing},
                    {"start": "2024-01-01", "end": "2024-12-31", "val": 550.0,
                     "frame": "CY2024", **one_filing},
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 600.0,
                     "frame": "CY2025", **one_filing},
                ]
            ),
            SHARES: _shares_unit(
                [
                    {"end": "2023-12-31", "val": 1000.0, "fp": "FY", **one_filing},
                    {"end": "2024-12-31", "val": 1010.0, "fp": "FY", **one_filing},
                    {"end": "2025-12-31", "val": 1020.0, "fp": "FY", **one_filing},
                ]
            ),
            SBC: _usd(
                [
                    {"start": "2023-01-01", "end": "2023-12-31", "val": 10.0,
                     "frame": "CY2023", **one_filing},
                    {"start": "2024-01-01", "end": "2024-12-31", "val": 12.0,
                     "frame": "CY2024", **one_filing},
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 15.0,
                     "frame": "CY2025", **one_filing},
                ]
            ),
        }
    )
    out = dilution_service.build(facts)
    # fy-keyed logic would collapse everything into a single "2025" row.
    assert out["years"] == ["2023", "2024", "2025"]
    assert [out["by_year"][y]["sbc"] for y in out["years"]] == [10.0, 12.0, 15.0]
    assert [out["by_year"][y]["shares_out"] for y in out["years"]] == [1000.0, 1010.0, 1020.0]
    assert out["by_year"]["2024"]["net_dilution_pct"] == pytest.approx(0.01)
    assert out["by_year"]["2025"]["net_dilution_pct"] == pytest.approx(round(10 / 1010, 4))


# --- (d) service wrapper: on-demand fetch + outage honesty -------------------
def _install_target(monkeypatch, cik="0000000123"):
    target = SimpleNamespace(cik=cik, name="Dilution Co")
    monkeypatch.setattr(dilution_service, "get_target", lambda s, w: target)
    return target


def test_wrapper_outage_is_unavailable_not_clean_empty(monkeypatch):
    _install_target(monkeypatch)
    monkeypatch.setattr(
        edgar_client,
        "get_company_facts",
        lambda *_a, **_k: (_ for _ in ()).throw(edgar_client.EdgarError("offline")),
    )
    out = dilution_service.dilution(object(), "ws1")
    assert out["status"] == "unavailable"
    assert out["years"] == []
    assert out["source_error"]
    assert out["workspace_id"] == "ws1"


def test_wrapper_builds_from_fetched_facts(monkeypatch):
    _install_target(monkeypatch)
    fetched: list[str] = []
    monkeypatch.setattr(
        edgar_client,
        "get_company_facts",
        lambda cik10: fetched.append(cik10) or _standard_facts(),
    )
    out = dilution_service.dilution(object(), "ws1")
    assert fetched == ["0000000123"]
    assert out["status"] == "available"
    assert out["target_name"] == "Dilution Co"
    assert out["source_error"] is None
    assert out["by_year"]["2024"]["net_dilution_pct"] == pytest.approx(0.02)


def test_target_without_cik_raises_not_found(monkeypatch):
    from src.services.common import NotFound

    _install_target(monkeypatch, cik=None)
    with pytest.raises(NotFound):
        dilution_service.dilution(object(), "ws1")


# --- (e) response-model contract ---------------------------------------------
def test_service_payload_round_trips_through_the_response_model(monkeypatch):
    from src.routers.peer_benchmark import router  # dilution lives on the market-context router
    from src.schemas.dilution import DilutionAnalysis

    assert any(r.path.endswith("/dilution") for r in router.routes)
    _install_target(monkeypatch)
    monkeypatch.setattr(edgar_client, "get_company_facts", lambda cik10: _standard_facts())
    model = DilutionAnalysis.model_validate(dilution_service.dilution(object(), "ws1"))
    assert model.status == "available"
    assert model.by_year["2024"].net_dilution_pct == pytest.approx(0.02)
    assert model.citations["2024"]["shares_out"].accession == "k2024"


# --- (f) live check (auto-skips offline) -------------------------------------
def test_live_dilution_build(client, live_workspace_id):
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        out = dilution_service.dilution(session, live_workspace_id)
    if out["status"] == "unavailable" and out.get("source_error"):
        pytest.skip(f"company facts unavailable live: {out['source_error']}")
    assert out["status"] in {"available", "partial"}
    assert out["years"]
    for year in out["years"]:
        row = out["by_year"][year]
        assert set(row) == {"shares_out", "sbc", "repurchases", "net_dilution_pct"}
    # Every citation binds a concept that matches the declared source for its field.
    for year_cites in out["citations"].values():
        for field, cite in year_cites.items():
            assert cite["concept"] == out["sources"][field]
