"""Faithfulness guardrail on REAL data: every citation must resolve; material findings are cited."""
from __future__ import annotations

from src.agents.citation_auditor import CitationAuditor


def _known(client, wid) -> set[str]:
    return {e["ref"] for e in client.get(f"/api/workspaces/{wid}/evidence").json()}


def test_risk_citations_resolve(client, live_workspace_id):
    known = _known(client, live_workspace_id)
    assert known
    for r in client.get(f"/api/workspaces/{live_workspace_id}/risks").json():
        assert r["evidence_ref"] in known, f"risk cites missing evidence {r['evidence_ref']}"
        if r["severity"] in ("high", "critical"):
            assert r["evidence_ref"], f"material risk '{r['title']}' is uncited"


def test_question_citations_resolve(client, live_workspace_id):
    known = _known(client, live_workspace_id)
    for q in client.get(f"/api/workspaces/{live_workspace_id}/questions").json():
        if q["evidence_ref"] is not None:
            assert q["evidence_ref"] in known


def test_memo_citations_resolve(client, live_workspace_id):
    known = _known(client, live_workspace_id)
    memo = client.get(f"/api/workspaces/{live_workspace_id}/memo").json()
    cited = CitationAuditor.extract_refs(memo["markdown_content"])
    assert cited
    assert not CitationAuditor.find_uncited(cited, known)


def test_bear_case_citations_resolve(client, live_workspace_id):
    known = _known(client, live_workspace_id)
    rt = client.get(f"/api/workspaces/{live_workspace_id}/red-team").json()
    cited = CitationAuditor.extract_refs(rt["bear_case_markdown"])
    assert not CitationAuditor.find_uncited(cited, known)
