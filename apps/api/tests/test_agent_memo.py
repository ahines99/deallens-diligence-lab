"""G59 — agent-drafted IC memo sections: per-section grounding, human accept/reject.

All offline: providers are scripted fakes popped from a factory once per section, so each
section's run is independently scripted. What these tests pin:

* per-section grounding ISOLATION — a section whose prose contains a number its own tool
  results never produced is withheld (no text served) while sibling sections keep their drafts;
* the draft state is an append-only ``agent_memo_draft`` ArtifactVersion; every human decision
  mints a NEW superseding version and never mutates the base draft;
* ``assembled_markdown`` exists only once every drafted section is decided and contains ONLY
  the human-ACCEPTED sections — withheld and rejected text never reaches the assembled memo;
* decisions require an authenticated actor (401-style), and stale/withheld/unknown sections
  are refused;
* mock mode and missing consent never construct a provider and persist NOTHING.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing, Workspace
from src.models.underwriting_data import ArtifactVersion
from src.services import agent_memo_service
from src.services.agent_memo_service import SECTION_PLAN
from src.services.common import NotFound

_CHUNK_TEXT = (
    "Customer concentration remains a material risk. Our largest customer represented "
    "approximately 14 percent of consolidated revenue during the fiscal year."
)


class _ScriptedProvider:
    model = "fake-memo-model"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)

    def complete_with_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        assert any(tool["name"] == "search_filings" for tool in tools)
        return self._responses.pop(0)


def _tool_use(name: str, arguments: dict) -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "tu_1", "name": name, "input": arguments}],
    }


def _final(text: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def _popping_factory(providers: list[_ScriptedProvider]):
    """One provider per section run — the G57 loop constructs one provider per run."""

    def _factory() -> _ScriptedProvider:
        return providers.pop(0)

    return _factory


_GROUNDED_OVERVIEW = (
    "The company's largest customer represents approximately 14 percent of consolidated "
    "revenue, a concentration the filings flag as material."
)
_RISKS_ANSWER = "The filings emphasize customer concentration as the dominant risk factor."
_UNDERWRITING_ANSWER = (
    "No underwriting cases have been saved yet, so the deterministic engine offers no view."
)


def _scripted_section_providers() -> list[_ScriptedProvider]:
    """Section-ordered scripts: grounded, FABRICATED, grounded (no-tool), grounded (no-tool)."""
    return [
        _ScriptedProvider(
            [
                _tool_use("search_filings", {"query": "customer concentration"}),
                _final(_GROUNDED_OVERVIEW),
            ]
        ),
        _ScriptedProvider(
            [
                _tool_use("search_filings", {"query": "revenue growth"}),
                _final("Revenue grew 37% year over year with expanding margins."),
            ]
        ),
        _ScriptedProvider([_final(_RISKS_ANSWER)]),
        _ScriptedProvider([_final(_UNDERWRITING_ANSWER)]),
    ]


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


@pytest.fixture()
def consenting_workspace(client) -> str:
    workspace_id = client.post(
        "/api/workspaces", json={"name": "Memo lab", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        filing = Filing(
            workspace_id=workspace_id,
            company_name="Memo Corp",
            ticker="MMO",
            cik="0000000059",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000059-25-000001",
            document_url="https://www.sec.gov/Archives/memo-10k.htm",
        )
        session.add(filing)
        session.flush()
        session.add(
            DocumentChunk(
                filing_id=filing.id,
                workspace_id=workspace_id,
                section="Item 1A Risk Factors",
                chunk_index=0,
                chunk_text=_CHUNK_TEXT,
                source_url=filing.document_url,
            )
        )
        session.commit()
    return workspace_id


@pytest.fixture()
def memo_client(client) -> TestClient:
    """The G59 router mounted standalone (it is not registered in main.py by this change)."""
    from src.routers import agent_memo

    app = FastAPI()
    app.include_router(agent_memo.router)

    @app.exception_handler(NotFound)
    async def _not_found(request, exc: NotFound) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(status_code=404, content={"detail": exc.message})

    return TestClient(app)


def _draft(workspace_id: str, providers: list[_ScriptedProvider] | None = None) -> dict:
    with SessionLocal() as session:
        return agent_memo_service.draft_sections(
            session,
            workspace_id,
            actor_id="analyst-1",
            provider_factory=_popping_factory(
                providers if providers is not None else _scripted_section_providers()
            ),
        )


def _decide(workspace_id: str, draft_id: str, section: str, decision: str, actor="analyst-1"):
    with SessionLocal() as session:
        return agent_memo_service.decide_section(
            session, workspace_id, draft_id, section, decision, actor_id=actor
        )


def _draft_rows(workspace_id: str) -> list[ArtifactVersion]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.workspace_id == workspace_id,
                    ArtifactVersion.artifact_type == "agent_memo_draft",
                )
                .order_by(ArtifactVersion.version)
            )
        )


def _sealed_agent_runs(workspace_id: str) -> list[ArtifactVersion]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ArtifactVersion).where(
                    ArtifactVersion.workspace_id == workspace_id,
                    ArtifactVersion.artifact_type == "agent_run",
                )
            )
        )


def test_grounded_and_fabricated_sections_coexist(live_mode, consenting_workspace):
    """The flagship guarantee: one fabricated section is withheld, its siblings survive."""
    record = _draft(consenting_workspace)
    assert record["status"] == "in_review"
    assert record["version"] == 1
    assert record["draft_artifact_id"]
    assert [s["section"] for s in record["sections"]] == list(SECTION_PLAN)
    assert [s["status"] for s in record["sections"]] == [
        "drafted",
        "withheld",
        "drafted",
        "drafted",
    ]
    assert all(s["decision"] == "pending" for s in record["sections"])

    overview = record["sections"][0]
    assert "14 percent" in overview["answer"]
    assert overview["grounding"]["grounded"] is True

    withheld = record["sections"][1]
    assert withheld["answer"] is None  # no text is served for a withheld section
    assert withheld["grounding"]["grounded"] is False
    assert any("37" in token for token in withheld["grounding"]["numeric_violations"])

    # Every section run sealed its own transcript, and the draft state itself is sealed.
    section_artifacts = {s["artifact_version_id"] for s in record["sections"]}
    assert None not in section_artifacts
    assert {row.id for row in _sealed_agent_runs(consenting_workspace)} == section_artifacts
    rows = _draft_rows(consenting_workspace)
    assert len(rows) == 1
    assert rows[0].content_json["status"] == "in_review"


def test_decisions_are_append_only_and_assemble_only_accepted_text(
    live_mode, consenting_workspace
):
    v1 = _draft(consenting_workspace)
    draft_id = v1["draft_artifact_id"]

    v2 = _decide(consenting_workspace, draft_id, "Business overview", "accept")
    assert v2["version"] == 2
    assert v2["status"] == "in_review"  # two drafted sections remain undecided
    decided = next(s for s in v2["sections"] if s["section"] == "Business overview")
    assert decided["decision"] == "accept"
    assert decided["decided_by"] == "analyst-1"

    # Append-only: the base draft was superseded, never mutated.
    rows = _draft_rows(consenting_workspace)
    assert [row.version for row in rows] == [1, 2]
    assert rows[1].supersedes_id == rows[0].id
    v1_sections = {s["section"]: s["decision"] for s in rows[0].content_json["sections"]}
    assert v1_sections["Business overview"] == "pending"

    # A superseded draft id is refused — decisions always land on the latest version.
    with pytest.raises(ValueError, match="superseded"):
        _decide(consenting_workspace, draft_id, "Key risks", "reject")
    # A withheld section has no draft to decide.
    with pytest.raises(ValueError, match="withheld"):
        _decide(consenting_workspace, v2["draft_artifact_id"], "Financial performance", "accept")
    with pytest.raises(ValueError, match="Unknown section"):
        _decide(consenting_workspace, v2["draft_artifact_id"], "Appendix", "accept")

    v3 = _decide(consenting_workspace, v2["draft_artifact_id"], "Key risks", "reject")
    assert v3["status"] == "in_review"
    v4 = _decide(consenting_workspace, v3["draft_artifact_id"], "Underwriting view", "accept")
    assert v4["status"] == "decided"

    assembled = v4["assembled_markdown"]
    assert "## Business overview" in assembled
    assert "14 percent" in assembled
    assert "## Underwriting view" in assembled
    assert _UNDERWRITING_ANSWER in assembled
    # ONLY accepted text: the rejected and withheld sections never reach the memo.
    assert "## Key risks" not in assembled
    assert _RISKS_ANSWER not in assembled
    assert "## Financial performance" not in assembled
    assert "37" not in assembled

    assert [row.version for row in _draft_rows(consenting_workspace)] == [1, 2, 3, 4]


def test_decisions_require_an_authenticated_actor(live_mode, consenting_workspace):
    v1 = _draft(consenting_workspace)
    with pytest.raises(ValueError, match="authenticated actor"):
        _decide(consenting_workspace, v1["draft_artifact_id"], "Business overview", "accept",
                actor=None)
    # Nothing was minted by the refused decision.
    assert [row.version for row in _draft_rows(consenting_workspace)] == [1]


def test_mock_mode_and_missing_consent_persist_nothing(client, monkeypatch):
    workspace_id = client.post(
        "/api/workspaces", json={"name": "Memo no consent", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        session.commit()

    def _factory():
        raise AssertionError("provider must not be constructed")

    # Mock mode (the CI default) — consent alone is not enough.
    with SessionLocal() as session:
        record = agent_memo_service.draft_sections(
            session, workspace_id, provider_factory=_factory
        )
    assert (record["status"], record["reason"]) == ("not_run", "mock")
    assert record["sections"] == [] and record["draft_artifact_id"] is None

    # Live mode without consent.
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = False
        session.commit()
    with SessionLocal() as session:
        record = agent_memo_service.draft_sections(
            session, workspace_id, provider_factory=_factory
        )
    assert (record["status"], record["reason"]) == ("not_run", "no_consent")

    # Nothing persisted anywhere: no draft state, no section transcripts, no latest draft.
    assert _draft_rows(workspace_id) == []
    assert _sealed_agent_runs(workspace_id) == []
    with SessionLocal() as session:
        assert agent_memo_service.get_latest_draft(session, workspace_id) is None


def test_route_contract(memo_client, consenting_workspace):
    # Mock CI default through the real route: honest not_run provenance, nothing persisted.
    response = memo_client.post(f"/api/workspaces/{consenting_workspace}/agent-memo/draft")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["reason"]) == ("not_run", "mock")
    assert body["sections"] == [] and body["draft_artifact_id"] is None

    # A not_run draft persists nothing, so the GET stays an honest 404, not a false-clean empty.
    assert memo_client.get(f"/api/workspaces/{consenting_workspace}/agent-memo").status_code == 404
    assert memo_client.get("/api/workspaces/nope/agent-memo").status_code == 404

    # Deciding without an actor is refused before anything is looked up.
    denied = memo_client.post(
        f"/api/workspaces/{consenting_workspace}/agent-memo/whatever/sections/decide",
        json={"section": "Business overview", "decision": "accept"},
    )
    assert denied.status_code == 401

    # With an actor, a bogus draft id is a 404 (NotFound), and a bad decision literal a 422.
    missing = memo_client.post(
        f"/api/workspaces/{consenting_workspace}/agent-memo/whatever/sections/decide",
        json={"section": "Business overview", "decision": "accept"},
        headers={"X-Actor-ID": "analyst-1"},
    )
    assert missing.status_code == 404
    invalid = memo_client.post(
        f"/api/workspaces/{consenting_workspace}/agent-memo/whatever/sections/decide",
        json={"section": "Business overview", "decision": "maybe"},
        headers={"X-Actor-ID": "analyst-1"},
    )
    assert invalid.status_code == 422


def test_decide_route_round_trip(live_mode, memo_client, consenting_workspace):
    v1 = _draft(consenting_workspace)
    fetched = memo_client.get(f"/api/workspaces/{consenting_workspace}/agent-memo")
    assert fetched.status_code == 200
    assert fetched.json()["draft_artifact_id"] == v1["draft_artifact_id"]

    response = memo_client.post(
        f"/api/workspaces/{consenting_workspace}/agent-memo/{v1['draft_artifact_id']}"
        "/sections/decide",
        json={"section": "Business overview", "decision": "accept"},
        headers={"X-Actor-ID": "reviewer-9"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2
    decided = next(s for s in body["sections"] if s["section"] == "Business overview")
    assert (decided["decision"], decided["decided_by"]) == ("accept", "reviewer-9")
