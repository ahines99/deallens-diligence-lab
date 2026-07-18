"""G56 — model-quality dashboard backend: one aggregation with honest per-section status.

Offline and deterministic. The core contract under test: every section carries an explicit
``status``, and absent data reads ``unavailable`` with a note — an empty judge-eval table must
never be dressed up as a 0% faithful rate.
"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from src.config import settings
from src.db.session import SessionLocal
from src.eval import agent_eval, calibration, harness
from src.models.eval_run import JudgeEvalRun
from src.models.underwriting_data import ArtifactVersion
from src.routers import model_ops
from src.services import judge_service, storage_service

_SECTIONS = (
    "judge_evals",
    "retrieval_metrics",
    "agent_evals",
    "calibration",
    "prompts",
    "extraction_comparison",
    "prompt_ab",
)
_PROMPT_IDS = {
    "memo_polish",
    "grounded_synthesis",
    "risk_extraction",
    "claim_extraction",
    "cross_corpus_synthesis",
    "diligence_agent",
}
_CONTEXT = "Revenue was $100 million in FY2025 [EV-001]."


def test_quality_endpoint_reports_every_section_with_a_status(client):
    body = client.get("/api/model-ops/quality").json()
    assert body["generated_at"]
    for section in _SECTIONS:
        assert section in body, f"missing section: {section}"
        assert body[section]["status"] in {"available", "unavailable"}
        assert "note" in body[section]


def test_empty_judge_eval_table_reads_unavailable_not_zeros(client):
    with SessionLocal() as session:
        persisted = session.scalar(select(func.count()).select_from(JudgeEvalRun))
    assert persisted == 0, "precondition: this test expects no judge evaluations in the shared DB"

    section = client.get("/api/model-ops/quality").json()["judge_evals"]
    assert section["status"] == "unavailable"
    assert section["note"] == "no judge evaluations persisted yet"
    # Honest absence: no fabricated zero counts alongside the unavailable status.
    assert "total" not in section
    assert "groups" not in section


def test_judge_evals_aggregate_globally_across_workspaces(client):
    with SessionLocal() as session:
        judge_service.judge_answer(
            session,
            question="q1",
            answer="Revenue was $100 million [EV-001].",  # faithful
            context=_CONTEXT,
            model_version="claude-test",
            prompt_version="v1",
            workspace_id="ws-quality-a",
        )
        judge_service.judge_answer(
            session,
            question="q2",
            answer="Revenue was $150 million [EV-001].",  # unfaithful
            context=_CONTEXT,
            model_version="claude-test",
            prompt_version="v1",
            workspace_id="ws-quality-b",
        )

    section = client.get("/api/model-ops/quality").json()["judge_evals"]
    assert section["status"] == "available"
    assert section["note"] is None
    assert section["total"] == 2  # both workspaces — the dashboard view is global
    assert section["faithful"] == 1
    assert section["faithful_rate"] == 0.5
    group = section["groups"][0]
    assert group["model_version"] == "claude-test"
    assert group["prompt_version"] == "v1"
    assert group["count"] == 2


def test_retrieval_metrics_reflect_the_committed_baseline(client):
    section = client.get("/api/model-ops/quality").json()["retrieval_metrics"]
    assert section["status"] == "available"
    baseline = harness.load_baseline()
    assert set(section["rankers"]) == set(harness.RANKERS) == {"bm25", "vector", "hybrid"}
    for ranker in harness.RANKERS:
        assert section["rankers"][ranker] == baseline["rankers"][ranker]
    assert section["num_questions"] == baseline["num_questions"]
    assert section["recall_ks"] == [1, 3, 5]


def test_retrieval_metrics_read_unavailable_when_baseline_unreadable(client, monkeypatch):
    def _missing() -> dict:
        raise OSError("baseline missing")

    monkeypatch.setattr(model_ops.harness, "load_baseline", _missing)
    section = client.get("/api/model-ops/quality").json()["retrieval_metrics"]
    assert section["status"] == "unavailable"
    assert "retrieval_metrics.json" in section["note"]
    assert "rankers" not in section


def test_agent_evals_reflect_the_committed_baseline(client):
    """G62: the section serves the committed scripted-provider baseline — metrics spread at the
    top level plus the case count, without the per-case transcript."""
    section = client.get("/api/model-ops/quality").json()["agent_evals"]
    assert section["status"] == "available"
    assert section["note"] is None
    baseline = agent_eval.load_baseline()
    assert section["cases"] == baseline["cases"]
    for name in agent_eval.METRIC_NAMES:
        assert section[name] == baseline["metrics"][name]
    assert "per_case" not in section  # the dashboard shows the aggregate, not the transcript


def test_agent_evals_read_unavailable_when_baseline_unreadable(client, monkeypatch):
    def _missing() -> dict:
        raise OSError("baseline missing")

    monkeypatch.setattr(model_ops.agent_eval, "load_baseline", _missing)
    section = client.get("/api/model-ops/quality").json()["agent_evals"]
    assert section["status"] == "unavailable"
    assert "agent_metrics.json" in section["note"]
    # Honest absence: no fabricated metrics or counts alongside the unavailable status.
    assert "cases" not in section
    assert all(name not in section for name in agent_eval.METRIC_NAMES)


def test_calibration_section_reports_the_active_threshold_and_study(client):
    section = client.get("/api/model-ops/quality").json()["calibration"]
    assert section["status"] == "available"
    assert section["partial_coverage_threshold"] == calibration.PARTIAL_COVERAGE_THRESHOLD == 0.5
    assert section["abstain_coverage"] == calibration.ABSTAIN_COVERAGE
    assert section["study"] == "src/eval/calibration_study.md"


def test_prompts_section_lists_the_registered_prompt_ids(client):
    section = client.get("/api/model-ops/quality").json()["prompts"]
    assert section["status"] == "available"
    assert {p["prompt_id"] for p in section["prompts"]} == _PROMPT_IDS
    assert all(len(p["prompt_hash"]) == 64 for p in section["prompts"])


def test_extraction_comparison_serves_the_newest_persisted_comparison(client):
    """G79: the section mirrors the newest persisted ``extraction_comparison`` artifact across
    workspaces. Seeds its own (append-only, immutable) artifact so the assertion is order-robust;
    the honest empty state and the full run+persist path are pinned in
    test_extraction_comparison.py, which runs before this module ever sees a persisted row."""
    workspace_id = client.post(
        "/api/workspaces", json={"name": "Quality comparison", "deal_type": "buyout"}
    ).json()["id"]
    content = {
        "workspace_id": workspace_id,
        "generated_at": "2026-07-18T00:00:00+00:00",
        "both": ["customer_concentration"],
        "llm_only": ["cyber_security"],
        "scanner_only": ["debt_liquidity"],
        "llm_provenance": {"engine": "llm", "reason": "applied"},
        "manifest": {"prompt_id": "risk_extraction"},
    }
    with SessionLocal() as session:
        session.add(
            ArtifactVersion(
                workspace_id=workspace_id,
                artifact_type="extraction_comparison",
                version=1,
                input_hash="0" * 64,
                content_hash="0" * 64,
                content_json=content,
                created_by="test",
            )
        )
        session.commit()

    section = client.get("/api/model-ops/quality").json()["extraction_comparison"]
    assert section == {
        "status": "available",
        "note": None,
        "workspace_id": workspace_id,
        "generated_at": content["generated_at"],
        "both": ["customer_concentration"],
        "llm_only": ["cyber_security"],
        "scanner_only": ["debt_liquidity"],
    }


def test_prompt_ab_reads_unavailable_until_a_report_exists(client, monkeypatch, tmp_path):
    """G81: an empty report store is an explicit absence with a note, not an empty report list."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    section = client.get("/api/model-ops/quality").json()["prompt_ab"]
    assert section["status"] == "unavailable"
    assert section["note"] == "no prompt A/B evaluations have been run yet"
    assert "reports" not in section


def test_prompt_ab_serves_the_newest_report_per_prompt(client, monkeypatch, tmp_path):
    """G81: the section surfaces the newest persisted report per registered prompt id, read
    straight from the blob envelope's newest-first history."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    newest = {"status": "completed", "prompt_id": "memo_polish", "winner": "a"}
    storage_service.get_store().put(
        "model-ops/prompt-ab/memo_polish.json",
        json.dumps({"history": [newest, {"status": "completed", "winner": "b"}]}).encode("utf-8"),
    )
    section = client.get("/api/model-ops/quality").json()["prompt_ab"]
    assert section["status"] == "available"
    assert section["note"] is None
    assert section["reports"] == [newest]
