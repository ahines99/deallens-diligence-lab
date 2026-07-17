"""G52 — LLM-first risk extraction with verbatim-span verification.

Every test is offline: canned-JSON fake providers stand in for the live LLM exactly as in
``test_structured_llm``, and live mode is faked by monkeypatching settings. The failure modes
guarded here are the ones that would let a hallucinating model into the governed record: a
fabricated or paraphrased quote becoming an Evidence row, an off-taxonomy category, an
out-of-range excerpt index, or mock CI silently reaching an LLM.
"""
from __future__ import annotations

import json

import pytest

from src.agents import llm_risk_extractor
from src.agents.risk_analyst import RiskAnalyst
from src.config import settings
from src.seed import loader


class _Chunk:
    """Duck-typed stand-in for DocumentChunk: the extractor reads only section + chunk_text."""

    def __init__(self, section: str, chunk_text: str) -> None:
        self.section = section
        self.chunk_text = chunk_text


class _FakeProvider:
    """Stands in for LiveProvider, returning canned JSON and counting calls."""

    model = "fake-model"

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self._response


# Fixture texts are crafted so the deterministic scanner's category per chunk is predictable
# (e.g. no stray "ai"/"margin"/"agency" signal substrings) — test 6 depends on the exact diff.
_CONC_TEXT = (
    "Customer concentration is a notable exposure. Our largest customer represented "
    "approximately 14 percent of consolidated revenue during the fiscal year."
)
_CONC_QUOTE = (
    "Our largest customer represented approximately 14 percent of consolidated revenue "
    "during the fiscal year."
)
_DEBT_TEXT = (
    "We must comply with each covenant under our senior credit agreement, and our "
    "liquidity could deteriorate if customer collections slow."
)
_CYBER_TEXT = (
    "An unauthorized third party accessed a limited set of customer support records, "
    "and forensic review of the affected systems is ongoing."
)

_FILING_CTX = {
    "company": "Fixture Corp",
    "url": "https://www.sec.gov/Archives/fixture-10k.htm",
    "date": "2025-02-01",
}


def _conc_chunk() -> _Chunk:
    return _Chunk("Item 1A Risk Factors", _CONC_TEXT)


def _finding(**overrides) -> dict:
    base = {
        "category": "customer_concentration",
        "title": "Revenue concentrated in a single customer",
        "finding": "One customer is ~14 percent of revenue, a dependency the filing flags.",
        "severity_score": 7,
        "quote": _CONC_QUOTE,
        "chunk_index": 0,
    }
    base.update(overrides)
    return base


def _response(*findings: dict) -> str:
    return json.dumps({"findings": list(findings)})


def _extract(chunks, provider: _FakeProvider):
    return llm_risk_extractor.extract(
        chunks,
        loader.risk_taxonomy(),
        _FILING_CTX,
        external_allowed=True,
        provider_factory=lambda: provider,
    )


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


def test_verified_finding_matches_scanner_shape(live_mode):
    """A span-verified LLM finding must be a drop-in for a scanner finding — key parity on the
    finding dict AND its evidence dict — or downstream Evidence/RiskFinding writes would break."""
    chunks = [_conc_chunk()]
    taxonomy = loader.risk_taxonomy()
    findings, provenance = _extract(chunks, _FakeProvider(_response(_finding())))

    assert provenance == {
        "engine": "llm",
        "reason": "applied",
        "manifest": provenance["manifest"],
        "proposed": 1,
        "verified": 1,
        "rejected": 0,
    }
    assert provenance["manifest"]["prompt_id"] == "risk_extraction"
    assert len(provenance["manifest"]["prompt_hash"]) == 64

    [llm_f] = findings
    scanner_f = next(
        f
        for f in RiskAnalyst().scan_text(chunks, taxonomy, _FILING_CTX)
        if f["risk_category"] == "customer_concentration"
    )
    assert set(llm_f) == set(scanner_f)
    assert set(llm_f["evidence"]) == set(scanner_f["evidence"])

    assert llm_f["risk_category"] == "customer_concentration"
    assert llm_f["risk_category_label"] == "Customer concentration"
    assert llm_f["workstream_owner"] == "commercial"
    assert llm_f["severity"] == "high" and llm_f["severity_score"] == 7
    assert llm_f["likelihood"] == "medium"  # no conditional/realized markers in the quote
    assert llm_f["confidence"] == 0.85  # span verified — above the scanner's 0.72 heuristic cap
    # The Evidence row carries the verified verbatim span and the LLM engine's audit identity.
    assert llm_f["evidence"]["evidence_text"] == _CONC_QUOTE
    assert llm_f["evidence"]["agent_name"] == "llm_risk_extractor"
    assert llm_f["evidence"]["source_section"] == "Item 1A Risk Factors"


def test_fabricated_or_paraphrased_quote_is_rejected(live_mode):
    """The core G52 guarantee: a quote that is not verbatim in the source chunk (here a close
    paraphrase) must never become a finding — it is dropped, counted, and the engine falls back
    to deterministic so the scanner serves the result."""
    paraphrase = _finding(quote="Our biggest customer was about 14% of revenue last year.")
    findings, provenance = _extract([_conc_chunk()], _FakeProvider(_response(paraphrase)))
    assert findings == []
    assert provenance["engine"] == "deterministic"
    assert provenance["reason"] == "no_verified_findings"
    assert provenance["proposed"] == 1
    assert provenance["verified"] == 0
    assert provenance["rejected"] == 1


def test_rejected_findings_do_not_sink_verified_ones(live_mode):
    """One fabricated proposal must not discard the batch: the verified finding survives, the
    fabricated one is rejected and counted, and the engine stays 'llm'."""
    fabricated = _finding(quote="Management expects churn to triple next quarter.")
    findings, provenance = _extract(
        [_conc_chunk()], _FakeProvider(_response(_finding(), fabricated))
    )
    assert [f["risk_category"] for f in findings] == ["customer_concentration"]
    assert provenance["engine"] == "llm"
    assert provenance["proposed"] == 2
    assert provenance["verified"] == 1
    assert provenance["rejected"] == 1


def test_whitespace_normalized_quote_verifies_but_recased_quote_does_not(live_mode):
    """Whitespace runs are the ONLY tolerance: a quote whose spacing/newlines differ from the
    source still verifies, but the same words re-cased must fail — case folding would let
    'cleaned up' (i.e. edited) quotes through the verbatim gate."""
    ragged = _Chunk(
        "Item 7 Management's Discussion and Analysis",
        "Our largest customer represented\n    approximately 14 percent of\nconsolidated revenue.",
    )
    smooth_quote = _finding(
        quote="Our largest customer represented approximately 14 percent of consolidated revenue."
    )
    findings, provenance = _extract([ragged], _FakeProvider(_response(smooth_quote)))
    assert provenance["verified"] == 1
    assert findings[0]["evidence"]["evidence_text"] == (
        "Our largest customer represented approximately 14 percent of consolidated revenue."
    )

    recased = _finding(
        quote="our largest customer represented approximately 14 percent of consolidated revenue."
    )
    findings, provenance = _extract([ragged], _FakeProvider(_response(recased)))
    assert findings == []
    assert provenance["rejected"] == 1


def test_unknown_category_and_bad_index_reject_while_severity_clamps(live_mode):
    """Taxonomy and locator discipline: an off-taxonomy slug or an excerpt index the model was
    never shown is rejected outright, while an out-of-range severity is clamped into 1..10
    rather than trusted (or allowed to sink an otherwise-verified finding)."""
    response = _response(
        _finding(category="alien_invasion"),  # not a taxonomy slug
        _finding(chunk_index=5),  # excerpt 5 was never sent to the model
        _finding(severity_score=99),  # clamps to 10
        _finding(severity_score=-3),  # clamps to 1
    )
    findings, provenance = _extract([_conc_chunk()], _FakeProvider(response))
    assert provenance["proposed"] == 4
    assert provenance["verified"] == 2
    assert provenance["rejected"] == 2
    assert sorted(f["severity_score"] for f in findings) == [1, 10]
    assert sorted(f["severity"] for f in findings) == ["high", "low"]


def test_mock_mode_falls_back_to_deterministic_without_calling_provider():
    """Hermetic-CI pin: in the default mock env the extractor must report the deterministic
    engine with reason 'mock' and never construct a provider — a regression here would mean
    CI can reach (and bill) a live LLM."""
    provider = _FakeProvider(_response(_finding()))
    findings, provenance = _extract([_conc_chunk()], provider)
    assert findings == []
    assert provenance == {
        "engine": "deterministic",
        "reason": "mock",
        "manifest": None,
        "proposed": 0,
        "verified": 0,
        "rejected": 0,
    }
    assert provider.calls == 0


def test_no_consent_falls_back_before_mode_is_even_consulted():
    """Consent outranks everything: without workspace consent the reason is 'no_consent' and no
    provider is constructed, matching the substrate's gating order."""
    provider = _FakeProvider(_response(_finding()))
    findings, provenance = llm_risk_extractor.extract(
        [_conc_chunk()],
        loader.risk_taxonomy(),
        _FILING_CTX,
        external_allowed=False,
        provider_factory=lambda: provider,
    )
    assert findings == []
    assert provenance["engine"] == "deterministic"
    assert provenance["reason"] == "no_consent"
    assert provider.calls == 0


def test_compare_with_scanner_reports_the_category_diff(live_mode):
    """The G52 comparison artifact: on a fixture where the scanner flags {concentration, debt}
    and the (fake) LLM verifies {concentration, cyber}, the diff must land each slug in exactly
    one of both/llm_only/scanner_only — miscounting would corrupt the recall-baseline story."""
    chunks = [
        _conc_chunk(),
        _Chunk("Item 1A Risk Factors", _DEBT_TEXT),
        _Chunk("Item 1A Risk Factors", _CYBER_TEXT),
    ]
    cyber = _finding(
        category="cyber_security",
        title="Unauthorized access to customer records",
        finding="The filing discloses unauthorized access to customer support records.",
        severity_score=6,
        quote=_CYBER_TEXT,
        chunk_index=2,
    )
    report = llm_risk_extractor.compare_with_scanner(
        chunks,
        loader.risk_taxonomy(),
        _FILING_CTX,
        external_allowed=True,
        provider_factory=lambda: _FakeProvider(_response(_finding(), cyber)),
    )
    assert report["both"] == ["customer_concentration"]
    assert report["llm_only"] == ["cyber_security"]
    assert report["scanner_only"] == ["debt_liquidity"]
    assert report["llm_provenance"]["engine"] == "llm"
    assert report["llm_provenance"]["verified"] == 2


# --- integration through analysis_service (sealed-run provenance) ---------------------------


def _latest_run(workspace_id: str):
    from sqlalchemy import select

    from src.db.session import SessionLocal
    from src.models.underwriting_data import AnalysisRun

    with SessionLocal() as session:
        return session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.workspace_id == workspace_id)
            .order_by(AnalysisRun.version.desc())
        ).first()


def test_sealed_run_records_extraction_provenance_and_mock_ci_never_calls_an_llm(client):
    """REQUIRED pin for hermetic CI: a full analysis in the default test env records
    output_summary['risk_extraction'] with engine 'deterministic' — reason 'no_consent' without
    workspace consent, and reason 'mock' even WITH consent — proving the scanner served the
    findings and no LLM was ever reached."""
    workspace_id = client.post(
        "/api/workspaces", json={"name": "G52 provenance", "deal_type": "buyout"}
    ).json()["id"]
    target = client.post(
        f"/api/workspaces/{workspace_id}/target",
        json={
            "name": "G52 Target",
            "target_type": "private_company",
            "revenue": 90_000_000,
            "revenue_growth": 0.07,
            "gross_margin": 0.52,
            "operating_margin": 0.11,
            "net_income": 6_000_000,
            "cash": 4_500_000,
            "total_debt": 22_000_000,
            "fiscal_year_end": "2025-12-31",
        },
    )
    assert target.status_code == 200, target.text

    # No consent: gated before mode is even consulted.
    assert client.post(f"/api/workspaces/{workspace_id}/risks/generate").status_code == 200
    provenance = _latest_run(workspace_id).output_summary["risk_extraction"]
    assert provenance["engine"] == "deterministic"
    assert provenance["reason"] == "no_consent"

    # With consent but LLM_MODE=mock (the CI default): still deterministic, reason 'mock'.
    from src.db.session import SessionLocal
    from src.models import Workspace

    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.data_classification = "internal"
        ws.external_llm_allowed = True
        session.commit()
    assert client.post(f"/api/workspaces/{workspace_id}/risks/generate").status_code == 200
    provenance = _latest_run(workspace_id).output_summary["risk_extraction"]
    assert provenance["engine"] == "deterministic"
    assert provenance["reason"] == "mock"
    assert provenance["manifest"] is None
