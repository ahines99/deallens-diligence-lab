"""G63 — comparative agent runs: unanimous consent, per-workspace scoping, honest merge.

All offline: scripted providers emit Anthropic-format blocks; a factory pops one provider per
workspace run (primary first, comps in caller order). What these tests pin:

* two consenting workspaces -> one sealed G57 run each, a deterministic merged markdown with one
  provenance-labeled section per workspace, and a union grounding verdict that (belt-and-braces)
  trivially passes;
* SCOPING PROOF: both workspaces' providers issue the SAME search query, yet each sealed
  transcript contains only its own workspace's chunk text — the harness, not the model, decides
  which workspace a tool call reads;
* one non-consenting (or restricted) workspace anywhere in the set -> the WHOLE run is
  ``not_run`` naming the blocking workspace, with zero provider constructions and nothing sealed
  (fail closed, never a silent exclusion);
* a rejected_ungrounded per-workspace run is rendered as an explicit withheld line and its
  fabricated number never reaches the merged markdown.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing, Workspace
from src.models.underwriting_data import ArtifactVersion
from src.services import agent_compare_service

_ALPHA_TEXT = (
    "Customer concentration remains material for Alpha Platforms. The largest customer "
    "represented approximately 14 percent of consolidated revenue. ALPHA-ONLY-EVIDENCE-TOKEN."
)
_BETA_TEXT = (
    "Customer concentration at Beta Logistics is modest. The top customer accounted for "
    "approximately 9 percent of consolidated revenue. BETA-ONLY-EVIDENCE-TOKEN."
)
_OBJECTIVE = "How concentrated is customer revenue?"


class _ScriptedProvider:
    model = "fake-agent-model"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)

    def complete_with_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        assert any(tool["name"] == "search_filings" for tool in tools)
        return self._responses.pop(0)


def _tool_use(name: str, arguments: dict, block_id: str = "tu_1") -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": block_id, "name": name, "input": arguments}],
    }


def _final(text: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def _popping_factory(providers: list[_ScriptedProvider]):
    """One provider per per-workspace run, in run order; counts constructions."""
    remaining = list(providers)
    constructed = {"count": 0}

    def factory():
        constructed["count"] += 1
        return remaining.pop(0)

    factory.constructed = constructed
    return factory


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


def _make_workspace(
    client,
    name: str,
    chunk_text: str,
    cik: str,
    *,
    consent: bool = True,
    classification: str | None = None,
) -> str:
    workspace_id = client.post(
        "/api/workspaces", json={"name": name, "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = consent
        if classification is not None:
            ws.data_classification = classification
        filing = Filing(
            workspace_id=workspace_id,
            company_name=name,
            ticker=name[:3].upper(),
            cik=cik,
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number=f"{cik}-25-000001",
            document_url=f"https://www.sec.gov/Archives/{cik}-10k.htm",
        )
        session.add(filing)
        session.flush()
        session.add(
            DocumentChunk(
                filing_id=filing.id,
                workspace_id=workspace_id,
                section="Item 1A Risk Factors",
                chunk_index=0,
                chunk_text=chunk_text,
                source_url=filing.document_url,
            )
        )
        session.commit()
    return workspace_id


@pytest.fixture()
def alpha_workspace(client) -> str:
    return _make_workspace(client, "Alpha Platforms", _ALPHA_TEXT, "0000000101")


@pytest.fixture()
def beta_workspace(client) -> str:
    return _make_workspace(client, "Beta Logistics", _BETA_TEXT, "0000000102")


def _run(primary: str, comps: list[str], factory, **kwargs):
    with SessionLocal() as session:
        return agent_compare_service.run_comparative_agent(
            session,
            primary,
            comps,
            kwargs.pop("objective", _OBJECTIVE),
            provider_factory=factory,
            **kwargs,
        )


def _sealed(workspace_id: str, artifact_type: str) -> list[ArtifactVersion]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ArtifactVersion).where(
                    ArtifactVersion.workspace_id == workspace_id,
                    ArtifactVersion.artifact_type == artifact_type,
                )
            )
        )


def _grounded_pair_factory():
    """Primary (Alpha) then comp (Beta): same query, each answers from its own evidence."""
    return _popping_factory(
        [
            _ScriptedProvider(
                [
                    _tool_use("search_filings", {"query": "customer concentration"}),
                    _final("The largest customer represents approximately 14 percent of revenue."),
                ]
            ),
            _ScriptedProvider(
                [
                    _tool_use("search_filings", {"query": "customer concentration"}),
                    _final("The top customer accounts for approximately 9 percent of revenue."),
                ]
            ),
        ]
    )


def test_two_consenting_workspaces_merge_with_provenance_and_union_grounding(
    live_mode, alpha_workspace, beta_workspace
):
    record = _run(alpha_workspace, [beta_workspace], _grounded_pair_factory())

    assert record["status"] == "completed"
    assert record["blocking_workspace_id"] is None
    assert [(e["role"], e["status"]) for e in record["per_workspace"]] == [
        ("primary", "completed"),
        ("comp", "completed"),
    ]

    # Each per-workspace run sealed its own transcript, referenced (not copied) in the record.
    for entry in record["per_workspace"]:
        runs = _sealed(entry["workspace_id"], "agent_run")
        assert [r.id for r in runs] == [entry["artifact_version_id"]]
        assert entry["tools_used"] == ["search_filings"]

    # Merged markdown: one provenance-labeled section per workspace, both grounded answers.
    merged = record["merged_markdown"]
    assert f"## Alpha Platforms ({alpha_workspace})" in merged
    assert f"## Beta Logistics ({beta_workspace})" in merged
    assert "approximately 14 percent" in merged
    assert "approximately 9 percent" in merged
    assert merged.index(f"## Alpha Platforms ({alpha_workspace})") < merged.index(
        f"## Beta Logistics ({beta_workspace})"
    )

    # Belt-and-braces: the deterministic merge trivially passes the union grounding gate.
    assert record["grounding"]["grounded"] is True
    assert record["grounding"]["numeric_violations"] == []

    # The comparative record is sealed on the PRIMARY workspace only.
    sealed = _sealed(alpha_workspace, "agent_comparative_run")
    assert len(sealed) == 1
    assert sealed[0].id == record["artifact_version_id"]
    assert sealed[0].content_json["status"] == "completed"
    assert _sealed(beta_workspace, "agent_comparative_run") == []


def test_scoping_proof_each_transcript_sees_only_its_own_workspace(
    live_mode, alpha_workspace, beta_workspace
):
    """Both providers issue the IDENTICAL query; the harness decides what each one reads."""
    record = _run(alpha_workspace, [beta_workspace], _grounded_pair_factory())
    assert record["status"] == "completed"

    by_ws = {e["workspace_id"]: e for e in record["per_workspace"]}
    alpha_steps = json.dumps(
        _sealed(alpha_workspace, "agent_run")[0].content_json["steps"]
    )
    beta_steps = json.dumps(_sealed(beta_workspace, "agent_run")[0].content_json["steps"])

    # Alpha's tool results contain ONLY Alpha's chunk text; Beta's ONLY Beta's — the model's
    # (identical) queries never cross the workspace boundary.
    assert "ALPHA-ONLY-EVIDENCE-TOKEN" in alpha_steps
    assert "BETA-ONLY-EVIDENCE-TOKEN" not in alpha_steps
    assert "BETA-ONLY-EVIDENCE-TOKEN" in beta_steps
    assert "ALPHA-ONLY-EVIDENCE-TOKEN" not in beta_steps
    assert by_ws[alpha_workspace]["artifact_version_id"] != by_ws[beta_workspace][
        "artifact_version_id"
    ]


def test_any_non_consenting_workspace_blocks_the_whole_run(
    live_mode, client, alpha_workspace
):
    no_consent = _make_workspace(
        client, "Gamma NoConsent", _BETA_TEXT, "0000000103", consent=False
    )
    factory = _popping_factory([])

    record = _run(alpha_workspace, [no_consent], factory)
    assert (record["status"], record["reason"]) == ("not_run", "no_consent")
    assert record["blocking_workspace_id"] == no_consent
    assert record["per_workspace"] == [] and record["merged_markdown"] is None

    # Restricted classification blocks identically, even with the consent flag set.
    restricted = _make_workspace(
        client, "Delta Restricted", _BETA_TEXT, "0000000104", classification="restricted"
    )
    record = _run(alpha_workspace, [restricted], factory)
    assert (record["status"], record["reason"]) == ("not_run", "no_consent")
    assert record["blocking_workspace_id"] == restricted

    # A blocked PRIMARY is named too (checked first).
    record = _run(no_consent, [alpha_workspace], factory)
    assert record["blocking_workspace_id"] == no_consent

    # Fail closed means NOTHING ran: zero providers, zero per-workspace runs, nothing sealed.
    assert factory.constructed["count"] == 0
    for ws_id in (alpha_workspace, no_consent, restricted):
        assert _sealed(ws_id, "agent_run") == []
        assert _sealed(ws_id, "agent_comparative_run") == []


def test_rejected_per_workspace_run_is_withheld_from_the_merge(
    live_mode, alpha_workspace, beta_workspace
):
    factory = _popping_factory(
        [
            _ScriptedProvider(
                [
                    _tool_use("search_filings", {"query": "customer concentration"}),
                    _final("The largest customer represents approximately 14 percent of revenue."),
                ]
            ),
            _ScriptedProvider(
                [
                    _tool_use("search_filings", {"query": "customer concentration"}),
                    # 23% appears in no tool result -> this run fails its own grounding gate.
                    _final("Churn is roughly 23% and the top customer is 9 percent of revenue."),
                ]
            ),
        ]
    )
    record = _run(alpha_workspace, [beta_workspace], factory)

    assert record["status"] == "completed"  # the comparison survives, honestly
    beta_entry = record["per_workspace"][1]
    assert beta_entry["workspace_id"] == beta_workspace
    assert beta_entry["status"] == "rejected_ungrounded"
    assert beta_entry["answer"] is None

    merged = record["merged_markdown"]
    assert f"## Beta Logistics ({beta_workspace})" in merged
    assert "_withheld/failed: rejected_ungrounded (grounding_failed)_" in merged
    assert "approximately 14 percent" in merged  # the grounded section survives
    # The fabricated number never reaches the merge. Assert against the merge CONTENT: the
    # section headers carry random workspace-id hex which may coincidentally contain "23".
    body = "\n".join(line for line in merged.splitlines() if not line.startswith("## "))
    assert "23" not in body
    assert "Churn" not in merged  # nor does any fragment of the rejected prose
    assert record["grounding"]["grounded"] is True

    # Beta's rejected transcript is still sealed in Beta's own workspace for audit.
    assert _sealed(beta_workspace, "agent_run")[0].content_json["status"] == (
        "rejected_ungrounded"
    )


def test_comp_id_validation_fails_closed(live_mode, alpha_workspace, beta_workspace):
    factory = _popping_factory([])
    with pytest.raises(ValueError, match="distinct"):
        _run(alpha_workspace, [beta_workspace, beta_workspace], factory)
    with pytest.raises(ValueError, match="primary"):
        _run(alpha_workspace, [alpha_workspace], factory)
    with pytest.raises(ValueError, match="Between 1 and 3"):
        _run(alpha_workspace, [], factory)
    with pytest.raises(ValueError, match="objective is required"):
        _run(alpha_workspace, [beta_workspace], factory, objective="   ")
    from src.services.common import NotFound

    with pytest.raises(NotFound):
        _run(alpha_workspace, ["does-not-exist"], factory)
    assert factory.constructed["count"] == 0


# --- Route contract ---------------------------------------------------------------------------
# The router is exercised on a local app until the integrator wires it into main.py
# (add `agent_compare` to the router imports/includes and `agent/compare` to the
# G58 `_LLM_CAPABLE_PATHS` alternation — those files are integrator-owned).


@pytest.fixture()
def compare_client(client) -> TestClient:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from src.routers import agent_compare
    from src.services.common import NotFound

    app = FastAPI()
    app.include_router(agent_compare.router)

    @app.exception_handler(NotFound)
    async def _not_found(request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    return TestClient(app)


def test_route_contract(compare_client, alpha_workspace, beta_workspace):
    # Mock CI default through the real route: honest not_run provenance, nothing sealed.
    response = compare_client.post(
        f"/api/workspaces/{alpha_workspace}/agent/compare",
        json={"objective": _OBJECTIVE, "comp_workspace_ids": [beta_workspace]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["reason"]) == ("not_run", "mock")
    assert body["per_workspace"] == [] and body["merged_markdown"] is None
    assert _sealed(alpha_workspace, "agent_comparative_run") == []

    url = f"/api/workspaces/{alpha_workspace}/agent/compare"
    # Service-level validation surfaces as 422.
    assert (
        compare_client.post(
            url, json={"objective": _OBJECTIVE, "comp_workspace_ids": [alpha_workspace]}
        ).status_code
        == 422
    )
    # Pydantic validation: empty objective, empty comp list, more than three comps.
    assert (
        compare_client.post(
            url, json={"objective": "", "comp_workspace_ids": [beta_workspace]}
        ).status_code
        == 422
    )
    assert (
        compare_client.post(url, json={"objective": _OBJECTIVE, "comp_workspace_ids": []})
        .status_code
        == 422
    )
    assert (
        compare_client.post(
            url,
            json={"objective": _OBJECTIVE, "comp_workspace_ids": ["a", "b", "c", "d"]},
        ).status_code
        == 422
    )
    # Unknown workspaces are 404s, not 500s.
    assert (
        compare_client.post(
            url, json={"objective": _OBJECTIVE, "comp_workspace_ids": ["missing-ws"]}
        ).status_code
        == 404
    )
