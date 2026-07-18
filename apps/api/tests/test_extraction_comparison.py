"""G79 — persisted extraction comparison: run-on-demand, sealed artifacts, honest quality view.

Every test is offline: canned-JSON fake providers stand in for the live LLM exactly as in
``test_llm_risk_extraction``, and live mode is faked by monkeypatching settings. Pinned here:

* mock CI / missing consent return ``not_run`` through the route with ZERO provider calls and
  persist nothing — the quality section stays honestly unavailable, never an empty "agreement";
* a consent-gated live run seals the category diff as an append-only ``ArtifactVersion``
  (``artifact_type="extraction_comparison"``) with generated_at + prompt manifest bound in;
* a second run supersedes the first (version 2) and the quality section serves the newest run;
* a workspace with no filing chunks refuses to compare before any provider is constructed.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from src.config import settings
from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing, Target, Workspace
from src.models.underwriting_data import ArtifactVersion
from src.services import extraction_comparison_service

# Fixture texts are crafted (as in test_llm_risk_extraction) so the deterministic scanner flags
# exactly {customer_concentration, debt_liquidity} across them, keeping the expected diff stable.
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


class _FakeProvider:
    """Stands in for LiveProvider, returning canned JSON and counting calls."""

    model = "fake-model"

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self._response


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


def _cyber_finding() -> dict:
    return _finding(
        category="cyber_security",
        title="Unauthorized access to customer records",
        finding="The filing discloses unauthorized access to customer support records.",
        severity_score=6,
        quote=_CYBER_TEXT,
        chunk_index=2,
    )


def _response(*findings: dict) -> str:
    return json.dumps({"findings": list(findings)})


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


def _workspace(client, *, consent: bool = True, with_chunks: bool = True) -> str:
    workspace_id = client.post(
        "/api/workspaces", json={"name": "G79 comparison", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        if consent:
            ws.external_llm_allowed = True
        session.add(Target(workspace_id=workspace_id, name="Fixture Corp"))
        filing = Filing(
            workspace_id=workspace_id,
            company_name="Fixture Corp",
            ticker="FIX",
            cik="0000000077",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000077-25-000001",
            document_url="https://www.sec.gov/Archives/fixture-10k.htm",
        )
        session.add(filing)
        session.flush()
        if with_chunks:
            for idx, text in enumerate((_CONC_TEXT, _DEBT_TEXT, _CYBER_TEXT)):
                session.add(
                    DocumentChunk(
                        filing_id=filing.id,
                        workspace_id=workspace_id,
                        section="Item 1A Risk Factors",
                        chunk_index=idx,
                        chunk_text=text,
                        source_url=filing.document_url,
                    )
                )
        session.commit()
    return workspace_id


def _run(workspace_id: str, provider: _FakeProvider) -> dict:
    with SessionLocal() as session:
        return extraction_comparison_service.run_and_persist(
            session, workspace_id, provider_factory=lambda: provider
        )


def _artifacts(workspace_id: str) -> list[ArtifactVersion]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.workspace_id == workspace_id,
                    ArtifactVersion.artifact_type == "extraction_comparison",
                )
                .order_by(ArtifactVersion.version)
            )
        )


def _global_count() -> int:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count())
            .select_from(ArtifactVersion)
            .where(ArtifactVersion.artifact_type == "extraction_comparison")
        )


# NOTE: file order matters — this first test pins the honest EMPTY state of the quality
# section, so it must run before any test in this module persists a comparison. ArtifactVersion
# rows are immutable (no cleanup possible), and only this module ever creates
# ``extraction_comparison`` artifacts, so the precondition holds in full-suite runs too.


def test_mock_mode_is_not_run_persists_nothing_and_quality_stays_unavailable(client):
    """Hermetic-CI pin: in the default mock env the route answers 200 with reason 'no_consent'
    before consent and 'mock' after, a directly-driven fake provider is never called, nothing is
    persisted, and the quality section reads unavailable with a note — not an empty diff."""
    assert _global_count() == 0, "precondition: no comparison artifacts in the shared DB yet"

    workspace_id = _workspace(client, consent=False)
    resp = client.post(f"/api/workspaces/{workspace_id}/extraction-comparison")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "not_run",
        "workspace_id": workspace_id,
        "reason": "no_consent",
    }

    with SessionLocal() as session:
        session.get(Workspace, workspace_id).external_llm_allowed = True
        session.commit()
    resp = client.post(f"/api/workspaces/{workspace_id}/extraction-comparison")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_run"
    assert resp.json()["reason"] == "mock"

    # Driving the service directly with a fake provider proves no provider is ever constructed.
    provider = _FakeProvider(_response(_finding()))
    result = _run(workspace_id, provider)
    assert result == {"status": "not_run", "workspace_id": workspace_id, "reason": "mock"}
    assert provider.calls == 0

    assert _artifacts(workspace_id) == []
    section = client.get("/api/model-ops/quality").json()["extraction_comparison"]
    assert section["status"] == "unavailable"
    assert section["note"] == "no extraction comparison has been run yet"
    assert "both" not in section


def test_workspace_without_chunks_refuses_before_any_provider_call(client, live_mode):
    """No filing text means there is nothing for either engine to read: the run reports
    'no_document_chunks' without constructing a provider and persists nothing."""
    workspace_id = _workspace(client, with_chunks=False)
    provider = _FakeProvider(_response(_finding()))
    result = _run(workspace_id, provider)
    assert result == {
        "status": "not_run",
        "workspace_id": workspace_id,
        "reason": "no_document_chunks",
    }
    assert provider.calls == 0
    assert _artifacts(workspace_id) == []


def test_unknown_workspace_is_404(client):
    resp = client.post("/api/workspaces/does-not-exist/extraction-comparison")
    assert resp.status_code == 404


def test_live_run_persists_the_diff_as_a_sealed_versioned_artifact(client, live_mode):
    """The G52 diff (scanner flags {concentration, debt}; the fake LLM verifies {concentration,
    cyber}) is sealed as extraction_comparison v1 with generated_at, the prompt manifest, and
    full extractor provenance bound into content_json."""
    workspace_id = _workspace(client)
    provider = _FakeProvider(_response(_finding(), _cyber_finding()))
    result = _run(workspace_id, provider)

    assert provider.calls == 1
    assert result["status"] == "available"
    assert result["both"] == ["customer_concentration"]
    assert result["llm_only"] == ["cyber_security"]
    assert result["scanner_only"] == ["debt_liquidity"]
    assert result["llm_provenance"]["engine"] == "llm"
    assert result["llm_provenance"]["verified"] == 2
    assert result["manifest"]["prompt_id"] == "risk_extraction"
    assert len(result["manifest"]["prompt_hash"]) == 64

    [artifact] = _artifacts(workspace_id)
    assert artifact.id == result["artifact_version_id"]
    assert artifact.version == result["version"] == 1
    assert artifact.supersedes_id is None
    content = artifact.content_json
    assert content["workspace_id"] == workspace_id
    assert content["generated_at"] == result["generated_at"]
    assert content["both"] == ["customer_concentration"]
    assert content["manifest"]["prompt_id"] == "risk_extraction"
    assert artifact.artifact_metadata["engine"] == "llm"


def test_second_run_supersedes_and_quality_serves_the_newest_comparison(client, live_mode):
    """Append-only discipline: a re-run mints version 2 superseding version 1 (v1 survives), and
    the quality dashboard's extraction_comparison section reflects the newest run only."""
    workspace_id = _workspace(client)
    _run(workspace_id, _FakeProvider(_response(_finding(), _cyber_finding())))

    section = client.get("/api/model-ops/quality").json()["extraction_comparison"]
    assert section["status"] == "available"
    assert section["note"] is None
    assert section["workspace_id"] == workspace_id
    assert section["llm_only"] == ["cyber_security"]

    # Second run: the fake LLM now verifies only the concentration finding.
    second = _run(workspace_id, _FakeProvider(_response(_finding())))
    assert second["version"] == 2
    v1, v2 = _artifacts(workspace_id)
    assert (v1.version, v2.version) == (1, 2)
    assert v2.supersedes_id == v1.id
    assert v1.content_json["llm_only"] == ["cyber_security"]  # history intact

    section = client.get("/api/model-ops/quality").json()["extraction_comparison"]
    assert section["status"] == "available"
    assert section["generated_at"] == second["generated_at"]
    assert section["both"] == ["customer_concentration"]
    assert section["llm_only"] == []
    assert section["scanner_only"] == ["debt_liquidity"]
