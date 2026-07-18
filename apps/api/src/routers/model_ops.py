"""Reproducible-LLM-ops endpoints: the hashed prompt registry (G10), the persisted
faithfulness-judge quality view (G05), the aggregated model-quality dashboard (G56), the
persisted extraction comparison (G79), prompt A/B evaluation (G81), and the committed
agent-eval baseline (G62).

The GET surface is read-only and deterministic. The two POST routes run consent-gated LLM
evaluations that fail closed: in mock/no-consent/no-key environments they return an honest
``not_run`` provenance with zero provider calls and persist nothing."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.eval import agent_eval, calibration, harness
from src.routers.deps import SessionDep
from src.services import (
    extraction_comparison_service,
    judge_service,
    prompt_ab_service,
    prompt_registry,
    storage_service,
)
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api", tags=["model-ops"])

# The committed calibration write-up backing the shipped abstention threshold (G06).
_CALIBRATION_STUDY = "src/eval/calibration_study.md"


class PromptABRequest(BaseModel):
    """Body for ``POST /api/model-ops/prompt-ab`` (G81)."""

    prompt_id: str
    candidate_template: str = Field(min_length=1)


@router.get("/model-ops/prompt-manifest")
def prompt_manifest() -> dict:
    """Versioned, hashed manifest for every registered LLM prompt (G10)."""
    return {"prompts": prompt_registry.all_manifests()}


@router.get("/workspaces/{workspace_id}/judge-evals")
def judge_evals(workspace_id: str, session: SessionDep) -> dict:
    """Per-model / per-prompt faithfulness quality view for a workspace's judged runs (G05)."""
    get_workspace_or_404(session, workspace_id)
    return judge_service.quality_summary(session, workspace_id=workspace_id)


@router.post("/workspaces/{workspace_id}/extraction-comparison")
def run_extraction_comparison(workspace_id: str, session: SessionDep) -> dict:
    """Run G52's extractor-vs-scanner comparison and persist it as a sealed artifact (G79).

    Mock mode, missing consent, a missing key, or a provider failure return 200 with an honest
    ``{"status": "not_run", "reason": ...}`` and persist nothing. NOTE: this path belongs in
    ``_LLM_CAPABLE_PATHS`` (G58) — it can trigger a live LLM call.
    """
    return extraction_comparison_service.run_and_persist(session, workspace_id)


@router.post("/model-ops/prompt-ab")
def run_prompt_ab(payload: PromptABRequest, session: SessionDep) -> dict:
    """A/B the registered template for ``prompt_id`` against a candidate over the golden set
    (G81). Mock/no-key environments return an honest ``not_run`` and persist nothing. NOTE: this
    path belongs in ``_LLM_CAPABLE_PATHS`` (G58) — it can trigger live LLM calls."""
    try:
        return prompt_ab_service.run_ab(session, payload.prompt_id, payload.candidate_template)
    except prompt_registry.UnknownPrompt as exc:
        raise HTTPException(
            status_code=422, detail=f"unknown prompt_id: {payload.prompt_id!r}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _judge_evals_section(session: Session) -> dict:
    """Global judge-eval quality summary — honest "unavailable" when nothing is persisted yet."""
    summary = judge_service.quality_summary(session)
    if summary["total"] == 0:
        return {"status": "unavailable", "note": "no judge evaluations persisted yet"}
    return {"status": "available", "note": None, **summary}


def _retrieval_metrics_section() -> dict:
    """The committed retrieval baseline (G03) — recall@k / MRR per ranker."""
    try:
        baseline = harness.load_baseline()
    except (OSError, ValueError):
        return {
            "status": "unavailable",
            "note": "committed retrieval baseline (src/eval/retrieval_metrics.json) is missing "
            "or unreadable",
        }
    return {"status": "available", "note": None, **baseline}


def _agent_evals_section() -> dict:
    """The committed agent-eval baseline (G62) — scripted-provider substrate metrics.

    The numbers measure the G57 harness pipeline (tool execution, grounding gate, budgets,
    sealing) over the committed golden objectives, never live model intelligence — providers
    in that eval are scripted by construction (see ``src/eval/agent_eval.py``).
    """
    try:
        baseline = agent_eval.load_baseline()
    except (OSError, ValueError):
        return {
            "status": "unavailable",
            "note": "committed agent eval baseline (src/eval/agent_metrics.json) is missing "
            "or unreadable",
        }
    return {
        "status": "available",
        "note": None,
        **baseline.get("metrics", {}),
        "cases": baseline.get("cases"),
    }


def _calibration_section() -> dict:
    """The shipped abstention/partial thresholds and the committed study behind them (G06)."""
    return {
        "status": "available",
        "note": None,
        "partial_coverage_threshold": calibration.PARTIAL_COVERAGE_THRESHOLD,
        "abstain_coverage": calibration.ABSTAIN_COVERAGE,
        "study": _CALIBRATION_STUDY,
    }


def _prompt_ab_section() -> dict:
    """Newest persisted A/B report per registered prompt (G81), honest when none exist."""
    try:
        reports = prompt_ab_service.latest_reports()
    except storage_service.BlobStoreError:
        return {"status": "unavailable", "note": "prompt A/B report storage is unreadable"}
    if not reports:
        return {"status": "unavailable", "note": "no prompt A/B evaluations have been run yet"}
    return {"status": "available", "note": None, "reports": reports}


@router.get("/model-ops/quality")
def model_quality(session: SessionDep) -> dict:
    """One aggregated model-quality view (G56), each section carrying an explicit source status.

    Absent data reads ``unavailable`` with a note — never fabricated zeros: an empty judge-eval
    table is "no evaluations yet", not a 0% faithful rate.
    """
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_evals": _judge_evals_section(session),
        "retrieval_metrics": _retrieval_metrics_section(),
        "agent_evals": _agent_evals_section(),
        "calibration": _calibration_section(),
        "prompts": {
            "status": "available",
            "note": None,
            "prompts": prompt_registry.all_manifests(),
        },
        "extraction_comparison": extraction_comparison_service.latest(session),
        "prompt_ab": _prompt_ab_section(),
    }
