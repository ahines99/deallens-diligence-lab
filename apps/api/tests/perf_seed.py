"""Shared perf fixtures for G37 (used by tests/test_perf_smoke.py and perf/k6_load_test.js).

Building the deterministic workspace + filing + chunks lives here so the in-process pytest smoke
and the standalone k6 seed step stay in sync. Run directly to seed a live server's DB and print
the workspace id for k6:

    LLM_MODE=mock AUTO_SEED=false AUTH_REQUIRED=false \
      DATABASE_URL=sqlite:///./perf.sqlite3 SCHEMA_MANAGEMENT=create_all \
      python -m tests.perf_seed
"""
from __future__ import annotations

# Mirrors the k6 script and the underwriting-model test's sample assumptions.
SAMPLE_ASSUMPTIONS = {
    "historical": {
        "ltm_revenue": 1_000.0,
        "ltm_ebitda": 200.0,
        "starting_cash": 50.0,
        "starting_net_working_capital": 100.0,
        "existing_debt": 100.0,
    },
    "transaction": {
        "close_date": "2026-01-01",
        "entry_multiple": 10.0,
        "exit_multiple": 10.0,
        "hold_period_years": 5.0,
        "transaction_fees": 50.0,
        "seller_rollover": 100.0,
        "minimum_cash": 25.0,
        "cash_sweep_percent": 1.0,
    },
    "projection": {
        "default_drivers": {
            "annual_revenue_growth": 0.08,
            "gross_margin": 0.60,
            "ebitda_margin": 0.20,
            "da_percent_revenue": 0.03,
            "capex_percent_revenue": 0.04,
            "net_working_capital_percent_revenue": 0.10,
            "cash_tax_rate": 0.25,
            "base_rate": 0.04,
        },
        "periods": [{"label": f"Y{year}", "months": 12} for year in range(1, 6)],
    },
    "debt_tranches": [
        {
            "name": "Revolver", "tranche_type": "revolver", "initial_amount": 0.0,
            "commitment": 150.0, "spread": 0.03, "cash_sweep_priority": 0,
        },
        {
            "name": "First Lien", "tranche_type": "term_loan", "initial_amount": 800.0,
            "senior": True, "spread": 0.04, "base_rate_floor": 0.05,
            "annual_amortization_rate": 0.02, "cash_sweep_priority": 10,
            "oid_discount": 0.02, "financing_fee_percent": 0.01,
        },
        {
            "name": "Mezzanine", "tranche_type": "mezzanine", "initial_amount": 200.0,
            "senior": False, "spread": 0.08, "pik_rate": 0.04, "cash_sweep_priority": 20,
        },
    ],
    "covenants": [
        {"name": "Total leverage", "metric": "total_leverage", "test": "maximum", "threshold": 4.0},
        {
            "name": "Interest coverage", "metric": "interest_coverage",
            "test": "minimum", "threshold": 2.0,
        },
    ],
    "valuation": {
        "discount_rate": 0.10, "terminal_growth_rate": 0.025, "mid_year_convention": True,
    },
}

# A well-formed BM25/search query and a QA question that both hit the fixture chunks.
SEARCH_QUERY = "revenue growth margin"
QA_QUESTION = "How concentrated is revenue in the largest customer?"


def build_perf_workspace(client) -> str:
    """Create a private workspace with two deterministic 10-K chunks; return its id (no network)."""
    from src.db.session import SessionLocal
    from src.models import DocumentChunk, Filing, Workspace

    ws_id = client.post(
        "/api/workspaces", json={"name": "Perf smoke fixture", "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        assert session.get(Workspace, ws_id) is not None
        filing = Filing(
            workspace_id=ws_id,
            company_name="Fixture Corp",
            ticker="FIX",
            cik="0000000001",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000001-25-000001",
            document_url="https://www.sec.gov/Archives/fixture-10k.htm",
            is_synthetic=False,
        )
        session.add(filing)
        session.flush()
        session.add_all(
            [
                DocumentChunk(
                    filing_id=filing.id,
                    workspace_id=ws_id,
                    section="Item 1A Risk Factors",
                    chunk_index=0,
                    chunk_text=(
                        "Customer concentration remains a material risk. Our largest customer "
                        "represented approximately 14 percent of consolidated revenue during "
                        "the fiscal year, and the loss of this customer would materially harm "
                        "our operating results."
                    ),
                ),
                DocumentChunk(
                    filing_id=filing.id,
                    workspace_id=ws_id,
                    section="Item 7 MD&A",
                    chunk_index=1,
                    chunk_text=(
                        "Revenue increased 12 percent year over year, driven primarily by "
                        "subscription growth. Operating expenses grew more slowly than revenue, "
                        "expanding operating margin by two percentage points."
                    ),
                ),
            ]
        )
        session.commit()
    return ws_id


def _main() -> None:
    from fastapi.testclient import TestClient

    from src.main import app

    with TestClient(app) as client:
        ws_id = build_perf_workspace(client)
    print(f"WORKSPACE_ID={ws_id}")


if __name__ == "__main__":
    _main()
