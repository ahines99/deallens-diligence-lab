"""G81 — prompt A/B evaluation: golden-set judging, blob persistence, promotion-gate honesty.

Every test is offline. A scripted provider answers through the same seam the live provider
would: the registered template (side A) echoes the judged extracts verbatim — fully grounded —
while the candidate (side B) fabricates numbers the golden context never states, so the
deterministic mock judge flags every B answer and the report must prefer A. Also pinned: the
blob envelope round-trip with its newest-first history cap, unknown-prompt/blank-candidate 422s,
workspace consent gating, and mock-mode honesty (no provider construction, nothing persisted,
quality section unavailable with a note).
"""
from __future__ import annotations

import hashlib
import json

import pytest

from src.config import settings
from src.db.session import SessionLocal
from src.models import Workspace
from src.services import prompt_ab_service, prompt_registry, storage_service

_PROMPT_ID = "grounded_synthesis"
_KEY = f"model-ops/prompt-ab/{_PROMPT_ID}.json"
_CANDIDATE = "You are a hasty analyst. Answer from memory in one confident sentence."


class _FakeABProvider:
    """Answers keyed off the system template it receives (the A/B seam under test): the
    registered template echoes the user prompt — every number it contains comes from the judged
    extracts — while the candidate fabricates figures absent from every golden context."""

    model = "fake-ab-model"

    def __init__(self) -> None:
        self.calls = 0
        self.systems: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.systems.append(system)
        if system == _CANDIDATE:
            return "Revenue was $999 million and churn tripled to 47 percent."
        return user


class _EchoBothProvider:
    """Grounded answers regardless of template — both sides judge identically (the tie case)."""

    model = "fake-ab-model"

    def complete(self, system: str, user: str) -> str:
        return user


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Every test gets a fresh, empty blob store so no A/B report leaks across tests (or into
    the repo's ./data/blobs default root)."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    return tmp_path


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    # Workspace-unbound A/B runs are golden-set-only and require the operator opt-in.
    monkeypatch.setattr(settings, "golden_eval_llm_allowed", True)


def _run(prompt_id: str = _PROMPT_ID, candidate: str = _CANDIDATE, provider=None, **kwargs):
    provider = provider if provider is not None else _FakeABProvider()
    with SessionLocal() as session:
        report = prompt_ab_service.run_ab(
            session, prompt_id, candidate, provider_factory=lambda: provider, **kwargs
        )
    return report, provider


def test_ab_report_prefers_the_faithful_registered_template(live_mode):
    """The core G81 contract: A answers grounded in the golden context, B fabricates numbers,
    so the judge scores A 1.0 vs B 0.0 over the documented 10-question subset and A wins."""
    spec = prompt_registry.get(_PROMPT_ID)
    report, provider = _run()

    assert report["status"] == "completed"
    assert report["prompt_id"] == _PROMPT_ID
    assert report["judge"] == "mock-faithfulness-v1"
    assert report["a"]["prompt_version"] == spec.prompt_version
    assert report["a"]["prompt_hash"] == spec.prompt_hash
    assert report["a"]["faithful_rate"] == 1.0
    assert report["a"]["judged"] == 10
    assert report["b"]["prompt_hash_candidate"] == (
        hashlib.sha256(_CANDIDATE.encode("utf-8")).hexdigest()
    )
    assert report["b"]["faithful_rate"] == 0.0
    assert report["b"]["judged"] == 10
    assert report["winner"] == "a"
    assert report["generated_at"]
    # Both templates went through the provider seam, once per judged question.
    assert provider.calls == 20
    assert provider.systems.count(spec.template) == 10
    assert provider.systems.count(_CANDIDATE) == 10


def test_identical_faithfulness_is_a_tie(live_mode):
    report, _ = _run(provider=_EchoBothProvider())
    assert report["a"]["faithful_rate"] == report["b"]["faithful_rate"] == 1.0
    assert report["winner"] == "tie"


def test_report_round_trips_through_the_blob_envelope_and_latest_reports(live_mode):
    """Persistence contract: the report lands under model-ops/prompt-ab/{prompt_id}.json as a
    {"history": [...]} envelope and latest_reports serves it for the quality view."""
    report, _ = _run()
    envelope = json.loads(storage_service.get_store().get(_KEY).decode("utf-8"))
    assert envelope == {"history": [report]}
    assert prompt_ab_service.latest_reports() == [report]


def test_history_is_newest_first_and_capped_at_twenty(live_mode):
    """A pre-seeded 20-deep history proves the cap: the new report lands at index 0, the oldest
    entry falls off, and the envelope never grows past 20."""
    seeded = [{"marker": i} for i in range(20)]  # marker 0 = newest of the seeds
    storage_service.get_store().put(
        _KEY, json.dumps({"history": seeded}).encode("utf-8")
    )
    report, _ = _run()
    history = json.loads(storage_service.get_store().get(_KEY).decode("utf-8"))["history"]
    assert len(history) == 20
    assert history[0] == report
    assert history[1] == {"marker": 0}
    assert history[-1] == {"marker": 18}  # marker 19 (the oldest seed) was dropped
    # latest_reports serves only the newest report per prompt, not the history.
    assert prompt_ab_service.latest_reports() == [report]


def test_unknown_prompt_id_and_blank_candidate_are_422(client):
    resp = client.post(
        "/api/model-ops/prompt-ab",
        json={"prompt_id": "not_registered", "candidate_template": _CANDIDATE},
    )
    assert resp.status_code == 422
    assert "not_registered" in resp.json()["detail"]

    resp = client.post(
        "/api/model-ops/prompt-ab",
        json={"prompt_id": _PROMPT_ID, "candidate_template": "   "},
    )
    assert resp.status_code == 422


def test_mock_mode_is_honest_and_persists_nothing(client):
    """Hermetic-CI pin: in the default mock env the route answers 200/not_run, a directly-driven
    fake provider is never constructed, and no blob is written."""
    resp = client.post(
        "/api/model-ops/prompt-ab",
        json={"prompt_id": _PROMPT_ID, "candidate_template": _CANDIDATE},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "not_run", "reason": "mock", "prompt_id": _PROMPT_ID}

    report, provider = _run()
    assert report == {"status": "not_run", "reason": "mock", "prompt_id": _PROMPT_ID}
    assert provider.calls == 0
    assert not storage_service.get_store().exists(_KEY)
    assert prompt_ab_service.latest_reports() == []


def test_workspace_unbound_live_run_requires_operator_opt_in(monkeypatch):
    """Every LLM path is consent-gated: without GOLDEN_EVAL_LLM_ALLOWED, a live workspace-unbound
    A/B refuses before any provider is constructed and persists nothing."""
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "golden_eval_llm_allowed", False)
    report, provider = _run()
    assert report == {"status": "not_run", "reason": "no_consent", "prompt_id": _PROMPT_ID}
    assert provider.calls == 0
    assert not storage_service.get_store().exists(_KEY)


def test_workspace_bound_run_requires_consent(client, live_mode):
    """A workspace-bound A/B inherits that workspace's consent semantics: no consent (or a
    restricted classification) refuses before any provider call and persists nothing."""
    workspace_id = client.post(
        "/api/workspaces", json={"name": "G81 consent", "deal_type": "buyout"}
    ).json()["id"]
    report, provider = _run(workspace_id=workspace_id)
    assert report == {"status": "not_run", "reason": "no_consent", "prompt_id": _PROMPT_ID}
    assert provider.calls == 0

    with SessionLocal() as session:
        ws = session.get(Workspace, workspace_id)
        ws.external_llm_allowed = True
        ws.data_classification = "restricted"
        session.commit()
    report, provider = _run(workspace_id=workspace_id)
    assert report["reason"] == "no_consent"
    assert provider.calls == 0
    assert not storage_service.get_store().exists(_KEY)


def test_quality_section_reflects_the_latest_report_per_prompt(client, live_mode):
    """The /quality prompt_ab section reads unavailable with a note until a report exists, then
    serves the newest report per registered prompt."""
    section = client.get("/api/model-ops/quality").json()["prompt_ab"]
    assert section["status"] == "unavailable"
    assert section["note"] == "no prompt A/B evaluations have been run yet"
    assert "reports" not in section

    report, _ = _run()
    section = client.get("/api/model-ops/quality").json()["prompt_ab"]
    assert section["status"] == "available"
    assert section["note"] is None
    assert section["reports"] == [report]
