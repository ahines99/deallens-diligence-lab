"""G57 — the diligence agent: a budget-capped tool-use loop over governed, read-only tools.

The agent orchestrates the workbench's existing services through a curated tool allowlist and
writes NOTHING to any governed record except its own sealed transcript. Deliberate boundaries:

* **Tools are read-only or pure compute.** The agent can search filings, ask the extractive QA,
  read risk findings/evidence/cases, and run an underwriting scenario in memory — it cannot
  mint evidence, approve claims, or advance workflow state. The four-eyes planes exist precisely
  so automation cannot approve its own work; the agent inherits that boundary wholesale.
* **The harness scopes every tool call.** The workspace id comes from the route, never from the
  model — a tool argument cannot reach another workspace.
* **Every run is sealed.** The full step transcript (tool calls, results, errors, the final
  answer, and the grounding verdict) becomes an append-only ``ArtifactVersion``
  (``artifact_type="agent_run"``); when the workspace links a deal, an ``agent.run_completed``
  audit event lands in the org outbox with actor attribution.
* **The final answer passes a fail-closed grounding gate.** Any quantity token or ``EV-###``
  reference in the answer that never appeared in a tool result's evidence projection (or the
  objective itself) rejects the answer — the transcript is still sealed, with the violations
  listed. The gate's source is each result's ``grounding_projection``, never the raw result:
  fields that echo the model's own arguments are excluded, so the agent cannot launder a
  fabricated figure through a tool round-trip. The agent's prose cannot smuggle numbers past
  the evidence discipline.
* **Budgets fail closed.** At most ``max_steps`` tool rounds (hard cap 16) and a per-result size
  cap; exhaustion seals the transcript with ``status="budget_exhausted"`` and no answer.

Consent gating matches every other LLM path: workspace ``external_llm_allowed`` and a
non-``restricted`` classification, live mode, and an API key — otherwise ``status="not_run"``
with a machine-readable reason and zero provider calls (mock CI never talks to a network).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.agents.citation_auditor import CitationAuditor
from src.agents.llm_provider import LiveProvider
from src.config import settings
from src.models.deal_workflow import Deal, WorkflowAuditEvent
from src.models.underwriting_data import ArtifactVersion
from src.services import prompt_registry
from src.services.agent_tools import _execute_tool, tool_definitions
from src.services.common import get_workspace_or_404, insert_versioned

logger = logging.getLogger("deallens.agent")

_MAX_OBJECTIVE_CHARS = 2_000
_HARD_STEP_CAP = 16
_ARTIFACT_TYPE = "agent_run"


# --- Grounding gate ---------------------------------------------------------------------------


def _grounding_verdict(answer: str, source_text: str) -> dict:
    """Fail closed on any quantity token or EV-### ref the tool results never produced."""
    answer_numbers = CitationAuditor.extract_quantity_tokens(answer)
    source_numbers = CitationAuditor.extract_quantity_tokens(source_text)
    numeric_violations = sorted((answer_numbers - source_numbers).elements())
    unknown_refs = sorted(
        CitationAuditor.extract_refs(answer) - CitationAuditor.extract_refs(source_text)
    )
    return {
        "grounded": not numeric_violations and not unknown_refs,
        "numeric_violations": numeric_violations,
        "unknown_refs": unknown_refs,
    }


# --- The loop ---------------------------------------------------------------------------------


def _not_run(
    workspace_id: str, objective: str, reason: str, client_request_id: str | None = None
) -> dict:
    return {
        "workspace_id": workspace_id,
        "objective": objective,
        "status": "not_run",
        "reason": reason,
        "answer": None,
        "steps": [],
        "tools_used": [],
        "steps_used": 0,
        "artifact_version_id": None,
        "manifest": None,
        "grounding": None,
        "client_request_id": client_request_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_diligence_agent(
    session,
    workspace_id: str,
    objective: str,
    *,
    actor_id: str | None = None,
    max_steps: int = 8,
    provider_factory=None,
    on_event=None,
    client_request_id: str | None = None,
) -> dict:
    """Run the tool loop for one objective; seal the transcript; return the full run record.

    ``on_event`` (G61 seam) receives ``{"type": "started"|"tool_step"|"finished", ...}`` dicts as
    the run progresses so an SSE route can stream the live timeline. Events are best-effort: a
    listener exception never affects the run, and the sealed artifact stays the source of truth.

    ``client_request_id`` (G61 recovery seam) is the caller-supplied idempotency key; it is
    recorded in the run record and the sealed artifact so a client recovering from an ambiguous
    network failure can match the sealed transcript to ITS OWN submission (and the routes can
    refuse or replay a duplicate) instead of re-running the agent.
    """

    def _emit(kind: str, payload: dict) -> None:
        if on_event is None:
            return
        try:
            on_event({"type": kind, **payload})
        except Exception:  # noqa: BLE001 - streaming is an observer, never a participant
            return

    ws = get_workspace_or_404(session, workspace_id)
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("An objective is required.")
    if len(objective) > _MAX_OBJECTIVE_CHARS:
        raise ValueError(f"Objective must be at most {_MAX_OBJECTIVE_CHARS} characters.")
    max_steps = max(1, min(int(max_steps), _HARD_STEP_CAP))

    external_allowed = ws.external_llm_allowed and ws.data_classification != "restricted"
    if not external_allowed:
        return _not_run(workspace_id, objective, "no_consent", client_request_id)
    if settings.is_mock:
        return _not_run(workspace_id, objective, "mock", client_request_id)
    if not settings.llm_api_key:
        return _not_run(workspace_id, objective, "no_api_key", client_request_id)

    spec = prompt_registry.get("diligence_agent")
    try:
        provider = (provider_factory or LiveProvider)()
    except Exception:
        return _not_run(workspace_id, objective, "error", client_request_id)
    manifest = prompt_registry.manifest("diligence_agent", model=provider.model)

    messages: list[dict] = [{"role": "user", "content": objective}]
    steps: list[dict] = []
    source_parts: list[str] = [objective]
    status = "completed"
    reason = "applied"
    answer: str | None = None
    _emit("started", {"workspace_id": workspace_id, "objective": objective})

    for _round in range(max_steps + 1):
        try:
            response = provider.complete_with_tools(
                spec.template, messages, tool_definitions()
            )
        except Exception:
            status, reason = "error", "provider_error"
            break
        content = response.get("content") or []
        text_blocks = [b.get("text", "") for b in content if b.get("type") == "text"]
        tool_blocks = [b for b in content if b.get("type") == "tool_use"]
        if response.get("stop_reason") != "tool_use" or not tool_blocks:
            answer = "\n".join(t for t in text_blocks if t).strip() or None
            if answer is None:
                status, reason = "error", "empty_answer"
            break
        if len(steps) + len(tool_blocks) > max_steps:
            status, reason = "budget_exhausted", "max_steps"
            break
        messages.append({"role": "assistant", "content": content})
        result_blocks: list[dict] = []
        for block in tool_blocks:
            name = block.get("name", "")
            arguments = block.get("input") or {}
            ok, result, grounding_text = _execute_tool(session, workspace_id, name, arguments)
            steps.append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "ok": ok,
                    "result": result if ok else None,
                    "error": None if ok else result,
                }
            )
            _emit("tool_step", {"step": steps[-1], "index": len(steps) - 1})
            if ok:
                # The gate's source is the curated evidence projection, never the raw result:
                # fields that echo the model's own arguments (H1: get_evidence.unresolved)
                # would let the answer launder fabricated figures through a tool round-trip.
                source_parts.append(grounding_text)
            result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.get("id", ""),
                    "content": json.dumps(result, default=str),
                    "is_error": not ok,
                }
            )
        messages.append({"role": "user", "content": result_blocks})
    else:
        status, reason = "budget_exhausted", "max_steps"

    grounding = None
    if status == "completed" and answer is not None:
        grounding = _grounding_verdict(answer, "\n".join(source_parts))
        if not grounding["grounded"]:
            # Fail closed: the transcript survives, the ungrounded prose does not.
            status, reason, answer = "rejected_ungrounded", "grounding_failed", None

    run_record = {
        "workspace_id": workspace_id,
        "objective": objective,
        "status": status,
        "reason": reason,
        "answer": answer,
        "steps": steps,
        "tools_used": sorted({step["tool"] for step in steps}),
        "steps_used": len(steps),
        "manifest": manifest,
        "grounding": grounding,
        "client_request_id": client_request_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Seal the transcript as an append-only artifact (the run's durable audit record).
    from src.services import underwriting_data_service

    def _build_artifact() -> ArtifactVersion:
        latest = session.scalar(
            select(ArtifactVersion)
            .where(
                ArtifactVersion.workspace_id == workspace_id,
                ArtifactVersion.artifact_type == _ARTIFACT_TYPE,
            )
            .order_by(ArtifactVersion.version.desc())
        )
        return ArtifactVersion(
            workspace_id=workspace_id,
            artifact_type=_ARTIFACT_TYPE,
            version=(latest.version + 1) if latest else 1,
            supersedes_id=latest.id if latest else None,
            analysis_run_id=None,
            source_snapshot_ids=[],
            input_hash=underwriting_data_service.content_hash(
                {"workspace_id": workspace_id, "objective": objective, "manifest": manifest}
            ),
            content_hash=underwriting_data_service.content_hash(run_record),
            content_json=run_record,
            content_text=None,
            file_uri=None,
            artifact_metadata={
                "status": status,
                "steps_used": len(steps),
                **(
                    {"client_request_id": client_request_id}
                    if client_request_id is not None
                    else {}
                ),
            },
            created_by=actor_id or "diligence_agent",
        )

    artifact = insert_versioned(session, _build_artifact)
    run_record["artifact_version_id"] = artifact.id

    # When the workspace links a deal, the run also lands in the org's append-only audit outbox
    # (webhook fan-out included) with actor attribution — mirroring the claim-extraction event.
    deal = session.scalar(select(Deal).where(Deal.workspace_id == workspace_id))
    if deal is not None:
        event = WorkflowAuditEvent(
            organization_id=deal.organization_id,
            deal_id=deal.id,
            actor_id=actor_id,
            actor_display_name=None,
            action="agent.run_completed",
            entity_type="ArtifactVersion",
            entity_id=artifact.id,
            detail={
                "status": status,
                "reason": reason,
                "steps_used": len(steps),
                "tools_used": run_record["tools_used"],
                "grounded": grounding["grounded"] if grounding else None,
            },
            request_id=None,
        )
        session.add(event)
        session.flush()
        from src.services import webhook_service

        webhook_service.queue_for_audit_event(session, event)

    session.commit()
    _emit(
        "finished",
        {
            "status": status,
            "reason": reason,
            "answer": answer,
            "grounding": grounding,
            "artifact_version_id": run_record["artifact_version_id"],
        },
    )
    return run_record
