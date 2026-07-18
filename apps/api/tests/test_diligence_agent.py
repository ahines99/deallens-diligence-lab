"""G57 — the diligence agent: governed tools, fail-closed grounding, budgets, consent.

All offline: providers are scripted fakes that emit Anthropic-format tool_use/text blocks.
What these tests pin:

* the loop executes ONLY allowlisted, workspace-scoped, read-only tools and records every step;
* the final answer is REJECTED (withheld, transcript still sealed) when it contains a quantity
  token or EV-### ref no tool result produced — the agent's prose cannot smuggle numbers;
* budgets fail closed; unknown tools / bad arguments / provider crashes never 500;
* mock mode, missing consent, and a restricted classification never construct a provider;
* every run seals an append-only ``agent_run`` ArtifactVersion.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.main import _LLM_CAPABLE_PATHS
from src.models import DocumentChunk, Filing, Workspace
from src.models.underwriting_data import ArtifactVersion
from src.services import agent_service

_CHUNK_TEXT = (
    "Customer concentration remains a material risk. Our largest customer represented "
    "approximately 14 percent of consolidated revenue during the fiscal year."
)


class _FakeToolLoopProvider:
    model = "fake-agent-model"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete_with_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        self.calls += 1
        assert any(tool["name"] == "search_filings" for tool in tools)
        return self._responses.pop(0)


def _tool_use(name: str, arguments: dict, block_id: str = "tu_1") -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": block_id, "name": name, "input": arguments}],
    }


def _final(text: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


@pytest.fixture()
def consenting_workspace(client) -> str:
    workspace_id = client.post(
        "/api/workspaces", json={"name": "Agent lab", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        filing = Filing(
            workspace_id=workspace_id,
            company_name="Agent Corp",
            ticker="AGT",
            cik="0000000042",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000042-25-000001",
            document_url="https://www.sec.gov/Archives/agent-10k.htm",
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


def _run(workspace_id: str, provider: _FakeToolLoopProvider, **kwargs):
    with SessionLocal() as session:
        return agent_service.run_diligence_agent(
            session,
            workspace_id,
            kwargs.pop("objective", "How concentrated is customer revenue?"),
            provider_factory=lambda: provider,
            **kwargs,
        )


def _sealed_runs(workspace_id: str) -> list[ArtifactVersion]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ArtifactVersion).where(
                    ArtifactVersion.workspace_id == workspace_id,
                    ArtifactVersion.artifact_type == "agent_run",
                )
            )
        )


def test_completed_run_grounds_answer_in_tool_results_and_seals_transcript(
    live_mode, consenting_workspace
):
    provider = _FakeToolLoopProvider(
        [
            _tool_use("search_filings", {"query": "customer concentration"}),
            _final(
                "The largest customer represents approximately 14 percent of revenue, a "
                "material concentration risk."
            ),
        ]
    )
    record = _run(consenting_workspace, provider)
    assert record["status"] == "completed"
    assert record["grounding"]["grounded"] is True
    assert "14 percent" in record["answer"]
    assert record["steps_used"] == 1
    assert record["steps"][0]["tool"] == "search_filings"
    assert record["steps"][0]["ok"] is True
    assert record["tools_used"] == ["search_filings"]
    assert record["manifest"]["prompt_id"] == "diligence_agent"
    sealed = _sealed_runs(consenting_workspace)
    assert len(sealed) == 1
    assert sealed[0].id == record["artifact_version_id"]
    assert sealed[0].content_json["status"] == "completed"


def test_fabricated_number_is_rejected_and_the_transcript_still_seals(
    live_mode, consenting_workspace
):
    """The flagship guarantee: prose with a number no tool produced is withheld, not served."""
    provider = _FakeToolLoopProvider(
        [
            _tool_use("search_filings", {"query": "customer concentration"}),
            _final("Churn is roughly 23% and the top customer is 14 percent of revenue."),
        ]
    )
    record = _run(consenting_workspace, provider)
    assert record["status"] == "rejected_ungrounded"
    assert record["answer"] is None
    assert record["grounding"]["grounded"] is False
    assert any("23" in token for token in record["grounding"]["numeric_violations"])
    sealed = _sealed_runs(consenting_workspace)
    assert len(sealed) == 1
    assert sealed[0].content_json["status"] == "rejected_ungrounded"


def test_unknown_tool_and_bad_arguments_are_recorded_errors_not_crashes(
    live_mode, consenting_workspace
):
    provider = _FakeToolLoopProvider(
        [
            _tool_use("drop_database", {"table": "everything"}),
            _tool_use("search_filings", {}),  # missing required query
            _final("The filings do not support an answer to this question."),
        ]
    )
    record = _run(consenting_workspace, provider)
    assert record["status"] == "completed"
    assert [step["ok"] for step in record["steps"]] == [False, False]
    assert "unknown tool" in record["steps"][0]["error"]
    assert "query is required" in record["steps"][1]["error"]


def test_step_budget_fails_closed(live_mode, consenting_workspace):
    provider = _FakeToolLoopProvider(
        [
            _tool_use("search_filings", {"query": "one"}),
            _tool_use("search_filings", {"query": "two"}),
            _final("never reached"),
        ]
    )
    record = _run(consenting_workspace, provider, max_steps=1)
    assert record["status"] == "budget_exhausted"
    assert record["answer"] is None
    assert record["steps_used"] == 1
    assert _sealed_runs(consenting_workspace)[0].content_json["status"] == "budget_exhausted"


def test_mock_mode_and_missing_consent_never_construct_a_provider(client, monkeypatch):
    workspace_id = client.post(
        "/api/workspaces", json={"name": "No consent", "deal_type": "buyout"}
    ).json()["id"]

    calls = {"constructed": 0}

    def _factory():
        calls["constructed"] += 1
        raise AssertionError("provider must not be constructed")

    # Mock mode (the CI default) — consent alone is not enough.
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        session.commit()
    with SessionLocal() as session:
        record = agent_service.run_diligence_agent(
            session, workspace_id, "objective", provider_factory=_factory
        )
    assert (record["status"], record["reason"]) == ("not_run", "mock")

    # Live mode without consent.
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = False
        session.commit()
    with SessionLocal() as session:
        record = agent_service.run_diligence_agent(
            session, workspace_id, "objective", provider_factory=_factory
        )
    assert (record["status"], record["reason"]) == ("not_run", "no_consent")

    # Live mode, consented, but restricted classification: confidential boxes stay closed.
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        ws.data_classification = "restricted"
        session.commit()
    with SessionLocal() as session:
        record = agent_service.run_diligence_agent(
            session, workspace_id, "objective", provider_factory=_factory
        )
    assert (record["status"], record["reason"]) == ("not_run", "no_consent")
    assert calls["constructed"] == 0
    assert _sealed_runs(workspace_id) == []  # not-run gatings are not sealed


def test_underwriting_scenario_tool_is_pure_compute(live_mode, consenting_workspace):
    assumptions = {
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
        "debt_tranches": [],
        "valuation": {
            "discount_rate": 0.10,
            "terminal_growth_rate": 0.025,
            "mid_year_convention": True,
        },
    }
    provider = _FakeToolLoopProvider(
        [
            _tool_use("run_underwriting_scenario", {"assumptions": assumptions}),
            _final("The debt-free scenario clears the underwriting screen."),
        ]
    )
    record = _run(consenting_workspace, provider, objective="Screen a debt-free scenario")
    assert record["status"] == "completed"
    step = record["steps"][0]
    assert step["ok"] is True
    assert step["result"]["returns"]["moic"] is not None
    # Pure compute: no case version was persisted by the tool.
    with SessionLocal() as session:
        from src.models.underwriting_model import UnderwritingCaseVersion

        assert (
            session.scalar(
                select(UnderwritingCaseVersion).where(
                    UnderwritingCaseVersion.workspace_id == consenting_workspace
                )
            )
            is None
        )


def test_route_contract_and_quota_classification(client, consenting_workspace):
    # Mock CI default through the real route: honest not_run provenance, never a network call.
    response = client.post(
        f"/api/workspaces/{consenting_workspace}/agent/run",
        json={"objective": "What are the top risks?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["reason"]) == ("not_run", "mock")
    assert body["steps"] == [] and body["answer"] is None

    # Objective validation surfaces as 422.
    assert (
        client.post(
            f"/api/workspaces/{consenting_workspace}/agent/run", json={"objective": ""}
        ).status_code
        == 422
    )

    # The route is in the G58 LLM-capable quota set.
    assert _LLM_CAPABLE_PATHS.match(f"/api/workspaces/{consenting_workspace}/agent/run")


# --- H1: the grounding source is a curated projection, never an argument echo ------------------


def test_grounding_gate_is_not_launderable_through_get_evidence_echo(
    live_mode, consenting_workspace
):
    """H1 regression: a fabricated EV-### ref passed as a get_evidence argument comes back in
    the tool's ``unresolved`` echo, which the model sees — but the grounding gate must not.
    An answer citing the fabricated ref and figures no tool produced is rejected."""
    provider = _FakeToolLoopProvider(
        [
            _tool_use("get_evidence", {"refs": ["EV-999"]}),
            _final("Per EV-999, revenue was $4.2 billion and grew 37 percent."),
        ]
    )
    record = _run(consenting_workspace, provider)
    # The tool call succeeded and echoed the unresolved ref to the MODEL (useful feedback)...
    assert record["steps"][0]["ok"] is True
    assert record["steps"][0]["result"]["unresolved"] == ["EV-999"]
    # ...but the echo never reaches the grounding source, so the answer fails closed.
    assert record["status"] == "rejected_ungrounded"
    assert record["answer"] is None
    assert record["grounding"]["grounded"] is False
    assert "EV-999" in record["grounding"]["unknown_refs"]


def test_get_evidence_refuses_refs_that_are_not_ev_shaped(live_mode, consenting_workspace):
    """Free text cannot ride through the refs argument at all: anything that is not EV-### is a
    tool error (fed back to the model, contributing nothing to the grounding source)."""
    provider = _FakeToolLoopProvider(
        [
            _tool_use("get_evidence", {"refs": ["$4.2 billion FY25 revenue"]}),
            _final("The filings do not support an answer to this question."),
        ]
    )
    record = _run(consenting_workspace, provider)
    assert record["status"] == "completed"
    assert record["steps"][0]["ok"] is False
    assert "EV-###" in record["steps"][0]["error"]


def test_grounding_projection_strips_only_argument_echo_fields():
    from src.services.agent_tools import grounding_projection

    result = {"evidence": [{"ref": "EV-1"}], "unresolved": ["EV-999"]}
    assert grounding_projection("get_evidence", result) == {"evidence": [{"ref": "EV-1"}]}
    untouched = {"results": [{"quote": "14 percent"}]}
    assert grounding_projection("search_filings", untouched) == untouched
