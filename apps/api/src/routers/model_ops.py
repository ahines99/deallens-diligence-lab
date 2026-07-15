"""Reproducible-LLM-ops read endpoints: the hashed prompt registry (G10) and the persisted
faithfulness-judge quality view (G05). Both are read-only and deterministic."""
from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.services import judge_service, prompt_registry
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api", tags=["model-ops"])


@router.get("/model-ops/prompt-manifest")
def prompt_manifest() -> dict:
    """Versioned, hashed manifest for every registered LLM prompt (G10)."""
    return {"prompts": prompt_registry.all_manifests()}


@router.get("/workspaces/{workspace_id}/judge-evals")
def judge_evals(workspace_id: str, session: SessionDep) -> dict:
    """Per-model / per-prompt faithfulness quality view for a workspace's judged runs (G05)."""
    get_workspace_or_404(session, workspace_id)
    return judge_service.quality_summary(session, workspace_id=workspace_id)
