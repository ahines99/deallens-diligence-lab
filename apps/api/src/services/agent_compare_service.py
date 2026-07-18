"""G63 — comparative agent runs: one objective across the target plus selected comp workspaces.

The comparative run is a thin, governed composition of G57 runs — deliberately NOT a new agent:

* **Scoping is inherited, never re-derived.** Phase 1 executes one full ``run_diligence_agent``
  per workspace; the harness passes each workspace id from THIS orchestrator, so every tool call
  a model makes is executed against its own workspace only. The model never chooses a workspace,
  and no tool result from workspace A is ever placed in workspace B's message stream.
* **Consent is unanimous and fail-closed.** EVERY involved workspace (primary + comps) must have
  ``external_llm_allowed`` and a non-``restricted`` classification, checked BEFORE any provider
  is constructed or any per-workspace run starts. One non-consenting workspace makes the whole
  comparative run ``not_run`` with ``blocking_workspace_id`` naming it — a silently-excluded
  workspace would misrepresent the comparison, so we refuse the run instead.
* **The merge is deterministic — no second LLM pass in v1.** Each per-workspace answer already
  passed its own fail-closed grounding gate against its own tool results; concatenating those
  answers under explicit per-workspace headers cannot introduce ungrounded content, whereas a
  cross-workspace synthesis call would create exactly the answer-blending risk G63 must exclude.
  A failed/rejected per-workspace run is recorded honestly and rendered as an explicit
  ``_withheld/failed: …_`` line — its (absent) answer contributes nothing to the merge.
* **The union grounding gate still runs (belt-and-braces).** The merged markdown is audited
  against the union of THIS run's sources only: the objective, the grounded answers, and the
  harness-authored section scaffold (headers carry workspace names/ids — harness provenance,
  not model output, but the auditor's tokenizer must see them). Raw tool results are
  deliberately NOT in the union source: the merge is built exclusively from answers and
  scaffold, and sealed step results can embed model-argument echoes (H1) that would only
  widen this gate. For a correct deterministic merge the union trivially passes; if a merge
  bug ever injected content no grounded answer produced, the run fails closed to
  ``rejected_ungrounded``.
* **The comparative record is sealed.** An append-only ``ArtifactVersion``
  (``artifact_type="agent_comparative_run"``) on the PRIMARY workspace carries the full record;
  each per-workspace transcript was already sealed by its own G57 run and is referenced here by
  ``artifact_version_id`` — provenance chains, it is not copied.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.config import settings
from src.models.underwriting_data import ArtifactVersion
from src.services import agent_service
from src.services.common import get_workspace_scoped_or_404, insert_versioned

logger = logging.getLogger("deallens.agent")

_ARTIFACT_TYPE = "agent_comparative_run"
_MAX_COMP_WORKSPACES = 3
_MAX_FRAMED_NAME_CHARS = 80
_WITHHELD_STATUSES = frozenset(
    {"rejected_ungrounded", "budget_exhausted", "error", "not_run"}
)


def _frame_objective(objective: str, workspace_name: str) -> str:
    """Per-workspace framing: same question, explicit single-workspace scope.

    Kept digit-free so the framing never adds quantity tokens to any grounding source.
    """
    name = (workspace_name or "").strip()[:_MAX_FRAMED_NAME_CHARS]
    return (
        f"{objective}\n\n"
        f'[Comparative-run context: you are analyzing the workspace "{name}" ONLY. '
        "Answer strictly from this workspace's own tool results; other workspaces are "
        "compared separately by the harness.]"
    )


# The per-workspace framing must keep the framed objective inside the G57 objective cap, so the
# comparative objective cap is the G57 cap minus the framing overhead. The budget is COMPUTED
# from the framing template at its worst case (a maximum-length workspace name) — a
# hand-maintained constant previously undercounted the overhead, letting a cap-accepted
# comparative objective overflow the G57 cap and degrade every per-workspace run to an opaque
# ``error (exception)``.
_FRAMING_BUDGET = len(_frame_objective("", "x" * _MAX_FRAMED_NAME_CHARS))
_MAX_OBJECTIVE_CHARS = agent_service._MAX_OBJECTIVE_CHARS - _FRAMING_BUDGET


def _not_run(
    primary_workspace_id: str,
    comp_workspace_ids: list[str],
    objective: str,
    reason: str,
    blocking_workspace_id: str | None = None,
) -> dict:
    return {
        "primary_workspace_id": primary_workspace_id,
        "comp_workspace_ids": comp_workspace_ids,
        "objective": objective,
        "status": "not_run",
        "reason": reason,
        "blocking_workspace_id": blocking_workspace_id,
        "per_workspace": [],
        "merged_markdown": None,
        "grounding": None,
        "artifact_version_id": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_comparative_agent(
    session,
    primary_workspace_id: str,
    comp_workspace_ids: list[str],
    objective: str,
    *,
    actor_id: str | None = None,
    organization_id: str | None = None,
    max_steps_per_workspace: int = 6,
    provider_factory=None,
) -> dict:
    """Run one objective across the primary plus 1..3 comp workspaces; seal the merged record.

    ``organization_id`` is the CALLER'S tenant (from the authenticated principal). Every
    involved workspace — primary and comps alike — is resolved through the tenant-scoped
    lookup, so naming another org's workspace as a comp returns the same 404 as a missing
    one (H2): no existence oracle, no consent-config leak, no cross-tenant read or seal.
    """
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("An objective is required.")
    if len(objective) > _MAX_OBJECTIVE_CHARS:
        raise ValueError(f"Objective must be at most {_MAX_OBJECTIVE_CHARS} characters.")
    comp_workspace_ids = [str(c).strip() for c in (comp_workspace_ids or []) if str(c).strip()]
    if not 1 <= len(comp_workspace_ids) <= _MAX_COMP_WORKSPACES:
        raise ValueError(
            f"Between 1 and {_MAX_COMP_WORKSPACES} comp workspaces are required."
        )
    if len(set(comp_workspace_ids)) != len(comp_workspace_ids):
        raise ValueError("Comp workspace ids must be distinct.")
    if primary_workspace_id in comp_workspace_ids:
        raise ValueError("A comp workspace cannot be the primary workspace.")

    # Existence within the caller's tenant first (NotFound -> 404), primary then comps in
    # caller order. The scoped lookup is the H2 boundary: a cross-tenant workspace id is
    # indistinguishable from a missing one, and it fails BEFORE any consent read or run.
    ordered = [
        (ws_id, get_workspace_scoped_or_404(session, ws_id, organization_id))
        for ws_id in [primary_workspace_id, *comp_workspace_ids]
    ]

    # Unanimous consent BEFORE any provider construction or per-workspace run: one blocked
    # workspace blocks the whole comparison (fail closed — never a silent exclusion).
    for ws_id, ws in ordered:
        if not (ws.external_llm_allowed and ws.data_classification != "restricted"):
            return _not_run(
                primary_workspace_id,
                comp_workspace_ids,
                objective,
                "no_consent",
                blocking_workspace_id=ws_id,
            )
    if settings.is_mock:
        return _not_run(primary_workspace_id, comp_workspace_ids, objective, "mock")
    if not settings.llm_api_key:
        return _not_run(primary_workspace_id, comp_workspace_ids, objective, "no_api_key")

    # --- Phase 1: one governed G57 run per workspace (primary first). Each run scopes its own
    # tool calls, applies its own grounding gate, and seals its own transcript.
    per_workspace: list[dict] = []
    for index, (ws_id, ws) in enumerate(ordered):
        role = "primary" if index == 0 else "comp"
        try:
            run = agent_service.run_diligence_agent(
                session,
                ws_id,
                _frame_objective(objective, ws.name),
                actor_id=actor_id,
                max_steps=max_steps_per_workspace,
                provider_factory=provider_factory,
            )
        except Exception:  # noqa: BLE001 - one workspace's crash must not kill the comparison
            logger.exception("Comparative per-workspace run failed (workspace %s)", ws_id)
            run = {
                "status": "error",
                "reason": "exception",
                "answer": None,
                "steps": [],
                "tools_used": [],
                "steps_used": 0,
                "artifact_version_id": None,
                "grounding": None,
            }
        per_workspace.append(
            {
                "workspace_id": ws_id,
                "workspace_name": ws.name,
                "role": role,
                "status": run["status"],
                "reason": run["reason"],
                "answer": run["answer"],
                "artifact_version_id": run["artifact_version_id"],
                "tools_used": run["tools_used"],
                "steps_used": run["steps_used"],
                "grounding": run["grounding"],
            }
        )

    # --- Phase 2: deterministic merge with explicit per-workspace provenance. No LLM sees the
    # merged text before the user does, so no cross-workspace blending can occur.
    sections: list[str] = []
    scaffold_parts: list[str] = []
    answer_parts: list[str] = []
    for entry in per_workspace:
        header = f"## {entry['workspace_name']} ({entry['workspace_id']})"
        if entry["status"] == "completed" and entry["answer"]:
            body = entry["answer"]
            answer_parts.append(body)
        else:
            body = f"_withheld/failed: {entry['status']} ({entry['reason']})_"
            scaffold_parts.append(body)
        sections.append(f"{header}\n\n{body}")
        scaffold_parts.append(header)
    merged_markdown: str | None = "\n\n".join(sections)

    # Union grounding (belt-and-braces): merged text vs the union of THIS run's sources only —
    # objective + the grounded answers + the harness scaffold. Tool results stay out: the merge
    # never draws on them directly, and their argument-echo fields must not widen this gate.
    union_source = "\n".join([objective, *answer_parts, *scaffold_parts])
    grounding = agent_service._grounding_verdict(merged_markdown, union_source)
    status, reason = "completed", "applied"
    if not grounding["grounded"]:  # pragma: no cover - unreachable for a correct merge
        status, reason, merged_markdown = (
            "rejected_ungrounded",
            "merge_grounding_failed",
            None,
        )

    record = {
        "primary_workspace_id": primary_workspace_id,
        "comp_workspace_ids": comp_workspace_ids,
        "objective": objective,
        "status": status,
        "reason": reason,
        "blocking_workspace_id": None,
        "per_workspace": per_workspace,
        "merged_markdown": merged_markdown,
        "grounding": grounding,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Seal the comparative record on the PRIMARY workspace (append-only, versioned).
    from src.services import underwriting_data_service

    def _build_artifact() -> ArtifactVersion:
        latest = session.scalar(
            select(ArtifactVersion)
            .where(
                ArtifactVersion.workspace_id == primary_workspace_id,
                ArtifactVersion.artifact_type == _ARTIFACT_TYPE,
            )
            .order_by(ArtifactVersion.version.desc())
        )
        return ArtifactVersion(
            workspace_id=primary_workspace_id,
            artifact_type=_ARTIFACT_TYPE,
            version=(latest.version + 1) if latest else 1,
            supersedes_id=latest.id if latest else None,
            analysis_run_id=None,
            source_snapshot_ids=[],
            input_hash=underwriting_data_service.content_hash(
                {
                    "primary_workspace_id": primary_workspace_id,
                    "comp_workspace_ids": comp_workspace_ids,
                    "objective": objective,
                }
            ),
            content_hash=underwriting_data_service.content_hash(record),
            content_json=record,
            content_text=None,
            file_uri=None,
            artifact_metadata={"status": status, "workspaces": len(per_workspace)},
            created_by=actor_id or "comparative_agent",
        )

    artifact = insert_versioned(session, _build_artifact)
    record["artifact_version_id"] = artifact.id
    session.commit()
    return record
