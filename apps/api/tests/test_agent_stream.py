"""G61 — streaming agent runs over SSE, plus the sealed-transcript rehydration listing.

All offline: the provider is a scripted fake injected through the documented
``src.routers.agent._provider_factory_override`` test seam. What these tests pin:

* the stream emits ``started`` → ``tool_step``… → ``finished`` frames in order, and the terminal
  ``finished`` frame carries the FULL sealed record (parity with the sealed artifact);
* gated runs (mock CI default) stream exactly one ``finished`` frame with the honest ``not_run``
  record and seal nothing;
* a worker-thread crash surfaces as a single terminal ``error`` frame, never a hung stream;
* ``GET .../agent/runs`` returns sealed transcripts newest-first with authoritative
  ``artifact_version_id`` stamping — the reconnect/replay source for a dropped stream.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing, Workspace
from src.models.underwriting_data import ArtifactVersion
from src.routers import agent as agent_router

_CHUNK_TEXT = (
    "Customer concentration remains a material risk. Our largest customer represented "
    "approximately 14 percent of consolidated revenue during the fiscal year."
)


class _FakeToolLoopProvider:
    model = "fake-agent-model"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)

    def complete_with_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        return self._responses.pop(0)


def _tool_use(name: str, arguments: dict, block_id: str = "tu_1") -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": block_id, "name": name, "input": arguments}],
    }


def _final(text: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def _frames(body: str) -> list[tuple[str, dict]]:
    """Parse ``event: <type>\\ndata: <json>\\n\\n`` frames into (type, payload) pairs."""
    parsed: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        event_type, data = "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event_type = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        parsed.append((event_type, json.loads(data)))
    return parsed


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


@pytest.fixture()
def consenting_workspace(client) -> str:
    workspace_id = client.post(
        "/api/workspaces", json={"name": "Agent stream lab", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        filing = Filing(
            workspace_id=workspace_id,
            company_name="Stream Corp",
            ticker="STRM",
            cik="0000000061",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000061-25-000001",
            document_url="https://www.sec.gov/Archives/stream-10k.htm",
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


def _install_provider(monkeypatch, responses: list[dict]) -> None:
    provider = _FakeToolLoopProvider(responses)
    monkeypatch.setattr(agent_router, "_provider_factory_override", lambda: provider)


def _stream_run(client, workspace_id: str, objective: str) -> tuple[list[tuple[str, dict]], dict]:
    with client.stream(
        "POST",
        f"/api/workspaces/{workspace_id}/agent/run-stream",
        json={"objective": objective},
    ) as resp:
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache"
        body = "".join(resp.iter_text())
    return _frames(body), dict(resp.headers)


def _sealed_runs(workspace_id: str) -> list[ArtifactVersion]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.workspace_id == workspace_id,
                    ArtifactVersion.artifact_type == "agent_run",
                )
                .order_by(ArtifactVersion.version)
            )
        )


def test_stream_emits_started_tool_step_finished_in_order(
    live_mode, consenting_workspace, client, monkeypatch
):
    """Acceptance: one scripted tool round streams as started → tool_step → finished."""
    _install_provider(
        monkeypatch,
        [
            _tool_use("search_filings", {"query": "customer concentration"}),
            _final(
                "The largest customer represents approximately 14 percent of revenue, a "
                "material concentration risk."
            ),
        ],
    )
    frames, _headers = _stream_run(
        client, consenting_workspace, "How concentrated is customer revenue?"
    )

    assert [event_type for event_type, _ in frames] == ["started", "tool_step", "finished"]

    started = frames[0][1]
    assert started["workspace_id"] == consenting_workspace
    assert started["objective"] == "How concentrated is customer revenue?"

    step_frame = frames[1][1]
    assert step_frame["index"] == 0
    assert step_frame["step"]["tool"] == "search_filings"
    assert step_frame["step"]["ok"] is True

    # The terminal frame is the FULL sealed record — byte-for-byte parity with the artifact.
    finished = frames[2][1]
    assert finished["status"] == "completed"
    assert "14 percent" in finished["answer"]
    assert finished["grounding"]["grounded"] is True
    assert finished["steps"] == [step_frame["step"]]
    sealed = _sealed_runs(consenting_workspace)
    assert len(sealed) == 1
    assert finished["artifact_version_id"] == sealed[0].id
    assert sealed[0].content_json["status"] == "completed"


def test_gated_run_streams_a_single_finished_frame_with_the_not_run_record(
    client, consenting_workspace
):
    """Mock CI default: no provider, no seal — one honest not_run finished frame."""
    frames, _headers = _stream_run(client, consenting_workspace, "What are the top risks?")
    assert [event_type for event_type, _ in frames] == ["finished"]
    record = frames[0][1]
    assert (record["status"], record["reason"]) == ("not_run", "mock")
    assert record["steps"] == [] and record["answer"] is None
    assert _sealed_runs(consenting_workspace) == []


def test_missing_workspace_404s_before_the_stream_opens(client):
    resp = client.post(
        "/api/workspaces/does-not-exist/agent/run-stream", json={"objective": "anything"}
    )
    assert resp.status_code == 404


def test_worker_crash_streams_a_single_terminal_error_frame(
    client, consenting_workspace, monkeypatch
):
    def _boom(*args, **kwargs):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(agent_router.agent_service, "run_diligence_agent", _boom)
    frames, _headers = _stream_run(client, consenting_workspace, "What are the top risks?")
    assert [event_type for event_type, _ in frames] == ["error"]
    assert frames[0][1]["detail"] == "worker exploded"


def test_runs_listing_returns_sealed_records_newest_first(
    live_mode, consenting_workspace, client, monkeypatch
):
    """The rehydration source: a dropped stream is recovered by reloading the sealed run."""
    for objective in ("First objective", "Second objective"):
        _install_provider(
            monkeypatch,
            [
                _tool_use("search_filings", {"query": "customer concentration"}),
                _final("The largest customer represents approximately 14 percent of revenue."),
            ],
        )
        frames, _headers = _stream_run(client, consenting_workspace, objective)
        assert frames[-1][0] == "finished"

    resp = client.get(f"/api/workspaces/{consenting_workspace}/agent/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert [run["objective"] for run in runs] == ["Second objective", "First objective"]

    # artifact_version_id is stamped from the sealing row — exact parity with the artifact.
    sealed = _sealed_runs(consenting_workspace)
    assert [run["artifact_version_id"] for run in runs] == [sealed[1].id, sealed[0].id]
    assert runs[0]["status"] == "completed"
    assert runs[0]["steps"][0]["tool"] == "search_filings"

    # limit is honored (and validated: 0 is rejected).
    limited = client.get(f"/api/workspaces/{consenting_workspace}/agent/runs?limit=1")
    assert [run["objective"] for run in limited.json()] == ["Second objective"]
    assert client.get(
        f"/api/workspaces/{consenting_workspace}/agent/runs?limit=0"
    ).status_code == 422

    # An unknown workspace 404s rather than returning an empty list.
    assert client.get("/api/workspaces/does-not-exist/agent/runs").status_code == 404
