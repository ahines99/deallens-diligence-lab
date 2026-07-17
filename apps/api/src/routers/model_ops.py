"""Reproducible-LLM-ops read endpoints: the hashed prompt registry (G10), the persisted
faithfulness-judge quality view (G05), and the aggregated model-quality dashboard (G56). All are
read-only and deterministic."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy.orm import Session

from src.eval import calibration, harness
from src.routers.deps import SessionDep
from src.services import judge_service, prompt_registry
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api", tags=["model-ops"])

# The committed calibration write-up backing the shipped abstention threshold (G06).
_CALIBRATION_STUDY = "src/eval/calibration_study.md"


@router.get("/model-ops/prompt-manifest")
def prompt_manifest() -> dict:
    """Versioned, hashed manifest for every registered LLM prompt (G10)."""
    return {"prompts": prompt_registry.all_manifests()}


@router.get("/workspaces/{workspace_id}/judge-evals")
def judge_evals(workspace_id: str, session: SessionDep) -> dict:
    """Per-model / per-prompt faithfulness quality view for a workspace's judged runs (G05)."""
    get_workspace_or_404(session, workspace_id)
    return judge_service.quality_summary(session, workspace_id=workspace_id)


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


def _calibration_section() -> dict:
    """The shipped abstention/partial thresholds and the committed study behind them (G06)."""
    return {
        "status": "available",
        "note": None,
        "partial_coverage_threshold": calibration.PARTIAL_COVERAGE_THRESHOLD,
        "abstain_coverage": calibration.ABSTAIN_COVERAGE,
        "study": _CALIBRATION_STUDY,
    }


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
        "calibration": _calibration_section(),
        "prompts": {
            "status": "available",
            "note": None,
            "prompts": prompt_registry.all_manifests(),
        },
        "extraction_comparison": {
            "status": "unavailable",
            "note": "populated when an LLM extraction comparison has been run (G52)",
        },
    }
