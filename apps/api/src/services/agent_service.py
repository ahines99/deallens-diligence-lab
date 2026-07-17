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
  reference in the answer that never appeared in a tool result (or the objective itself) rejects
  the answer — the transcript is still sealed, with the violations listed. The agent's prose
  cannot smuggle numbers past the evidence discipline.
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

from pydantic import ValidationError
from sqlalchemy import select

from src.agents.citation_auditor import CitationAuditor
from src.agents.llm_provider import LiveProvider
from src.config import settings
from src.models import RiskFinding, Target
from src.models.deal_workflow import Deal, WorkflowAuditEvent
from src.models.underwriting_data import ArtifactVersion
from src.models.underwriting_model import UnderwritingCaseVersion
from src.services import (
    evidence_service,
    filings_qa_service,
    prompt_registry,
    retrieval_service,
)
from src.services.common import get_workspace_or_404, insert_versioned

logger = logging.getLogger("deallens.agent")

_MAX_OBJECTIVE_CHARS = 2_000
_HARD_STEP_CAP = 16
# Serialized tool results larger than this are truncated with an explicit marker: the model sees
# less, never something fabricated.
_MAX_RESULT_CHARS = 6_000
_ARTIFACT_TYPE = "agent_run"


# --- Governed tool registry -------------------------------------------------------------------
# Every handler takes (session, workspace_id, arguments) and returns a JSON-safe dict. Handlers
# are read-only or pure compute by construction — adding a mutating tool here is a design
# decision, not an oversight; see the module docstring before doing so.


def _tool_get_workspace_overview(session, workspace_id: str, arguments: dict) -> dict:
    ws = get_workspace_or_404(session, workspace_id)
    target = session.scalar(select(Target).where(Target.workspace_id == workspace_id))
    fin = (target.financials or {}) if target else {}
    return {
        "workspace": {
            "name": ws.name,
            "deal_type": ws.deal_type,
            "investment_question": ws.investment_question,
            "status": ws.status,
        },
        "target": (
            {
                "name": target.name,
                "ticker": target.ticker,
                "sector": target.sector,
                "fiscal_year_end": target.fiscal_year_end,
                "data_source": target.data_source,
                "revenue": target.revenue,
                "revenue_growth": target.revenue_growth,
                "gross_margin": target.gross_margin,
                "operating_margin": target.operating_margin,
                "net_income": target.net_income,
                "cash": target.cash,
                "total_debt": target.total_debt,
            }
            if target
            else None
        ),
        "trends": (fin.get("trends") or {}).get("rows", [])[-5:],
    }


def _tool_search_filings(session, workspace_id: str, arguments: dict) -> dict:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    k = max(1, min(int(arguments.get("k") or 5), 10))
    if retrieval_service.workspace_has_embeddings(session, workspace_id):
        retrieved = retrieval_service.retrieve_hybrid(session, workspace_id, query, k=k)
    else:
        retrieved = retrieval_service.retrieve(session, workspace_id, query, k=k)
    return {
        "results": [
            {
                "section": item.chunk.section,
                "chunk_index": item.chunk.chunk_index,
                "quote": (item.chunk.chunk_text or "")[:600],
                "score": item.score,
            }
            for item in retrieved
        ]
    }


def _tool_ask_filings_qa(session, workspace_id: str, arguments: dict) -> dict:
    question = str(arguments.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    result = filings_qa_service.ask(session, workspace_id, question)
    return {
        "status": result.get("status"),
        "answer": result.get("answer"),
        "citations": [
            {key: citation.get(key) for key in ("section", "quote", "source_name")}
            for citation in (result.get("citations") or [])[:6]
        ],
    }


def _tool_list_risk_findings(session, workspace_id: str, arguments: dict) -> dict:
    findings = session.scalars(
        select(RiskFinding)
        .where(RiskFinding.workspace_id == workspace_id)
        .order_by(RiskFinding.severity_score.desc())
    ).all()
    return {
        "findings": [
            {
                "title": finding.title,
                "category": finding.risk_category_label,
                "severity": finding.severity,
                "severity_score": finding.severity_score,
                "finding": (finding.finding or "")[:400],
                "evidence_ref": finding.evidence_ref,
            }
            for finding in findings[:15]
        ]
    }


def _tool_get_evidence(session, workspace_id: str, arguments: dict) -> dict:
    refs = arguments.get("refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("refs must be a non-empty list of EV-### strings")
    wanted = {str(ref).strip() for ref in refs}
    rows = [
        row
        for row in evidence_service.list_evidence(session, workspace_id)
        if row.ref in wanted
    ]
    return {
        "evidence": [
            {
                "ref": row.ref,
                "claim": row.claim,
                "evidence_text": (row.evidence_text or "")[:400],
                "source_name": row.source_name,
                "confidence": row.confidence,
            }
            for row in rows
        ],
        "unresolved": sorted(wanted - {row.ref for row in rows}),
    }


def _tool_list_underwriting_cases(session, workspace_id: str, arguments: dict) -> dict:
    versions = session.scalars(
        select(UnderwritingCaseVersion)
        .where(UnderwritingCaseVersion.workspace_id == workspace_id)
        .order_by(UnderwritingCaseVersion.case_key, UnderwritingCaseVersion.version.desc())
    ).all()
    latest: dict[str, UnderwritingCaseVersion] = {}
    for version in versions:
        latest.setdefault(version.case_key, version)
    out = []
    for case in latest.values():
        returns = (case.result or {}).get("returns") or {}
        out.append(
            {
                "case_key": case.case_key,
                "label": case.label,
                "version": case.version,
                "irr": returns.get("xirr"),
                "moic": returns.get("moic"),
                "created_by": case.created_by,
            }
        )
    return {"cases": out}


def _tool_run_underwriting_scenario(session, workspace_id: str, arguments: dict) -> dict:
    # Pure computation: nothing is persisted — the analyst (not the agent) decides whether a
    # scenario becomes a governed case version through the normal four-eyes flow.
    from src.schemas.underwriting_model import UnderwritingAssumptions
    from src.services import underwriting_model_service

    assumptions = arguments.get("assumptions")
    if not isinstance(assumptions, dict):
        raise ValueError("assumptions must be an UnderwritingAssumptions object")
    try:
        parsed = UnderwritingAssumptions.model_validate(assumptions)
        result = underwriting_model_service.run_underwriting(parsed)
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"scenario failed: {exc}") from exc
    return {
        "returns": {
            "irr": result.returns.xirr,
            "moic": result.returns.moic,
            "sponsor_exit_proceeds": result.returns.sponsor_exit_proceeds,
        },
        "summary": {
            "revenue_cagr": result.summary.revenue_cagr,
            "exit_ebitda": result.summary.exit_ebitda,
            "maximum_total_leverage": result.summary.maximum_total_leverage,
            "minimum_liquidity": result.summary.minimum_liquidity,
            "first_covenant_breach": result.summary.first_covenant_breach,
            "first_debt_service_default": result.summary.first_debt_service_default,
        },
        "dcf_equity_value": result.dcf.equity_value,
    }


_TOOLS: dict[str, tuple[str, dict, callable]] = {
    "get_workspace_overview": (
        "Workspace, target, headline financials, and recent revenue/margin trend rows.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_get_workspace_overview,
    ),
    "search_filings": (
        "Ranked verbatim excerpts from the ingested SEC filings for a query.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        _tool_search_filings,
    ),
    "ask_filings_qa": (
        "The workbench's extractive, abstaining Q&A over the filings (cited or abstained).",
        {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        _tool_ask_filings_qa,
    ),
    "list_risk_findings": (
        "Current risk findings with severities and EV-### evidence references.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_list_risk_findings,
    ),
    "get_evidence": (
        "Resolve specific EV-### references to their underlying evidence rows.",
        {
            "type": "object",
            "properties": {"refs": {"type": "array", "items": {"type": "string"}}},
            "required": ["refs"],
            "additionalProperties": False,
        },
        _tool_get_evidence,
    ),
    "list_underwriting_cases": (
        "Latest saved underwriting case versions with headline IRR/MoIC.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_list_underwriting_cases,
    ),
    "run_underwriting_scenario": (
        "Run the deterministic LBO/DCF engine on supplied assumptions IN MEMORY (nothing is "
        "saved) and return headline returns and covenant outcomes.",
        {
            "type": "object",
            "properties": {"assumptions": {"type": "object"}},
            "required": ["assumptions"],
            "additionalProperties": False,
        },
        _tool_run_underwriting_scenario,
    ),
}


def tool_definitions() -> list[dict]:
    """Anthropic-format tool declarations for the loop (and the contract tests)."""
    return [
        {"name": name, "description": description, "input_schema": schema}
        for name, (description, schema, _handler) in _TOOLS.items()
    ]


def _execute_tool(session, workspace_id: str, name: str, arguments: dict) -> tuple[bool, dict | str]:
    """Run one governed tool; (ok, result-or-error). Errors go back to the model, never raise."""
    spec = _TOOLS.get(name)
    if spec is None:
        return False, f"unknown tool: {name!r}"
    _description, _schema, handler = spec
    try:
        result = handler(session, workspace_id, arguments or {})
    except ValueError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - a tool crash must not kill the sealed run
        logger.warning("Agent tool '%s' failed: %s", name, exc)
        return False, f"tool failed: {exc}"
    serialized = json.dumps(result, default=str)
    if len(serialized) > _MAX_RESULT_CHARS:
        return True, {
            "truncated": True,
            "note": f"result truncated to {_MAX_RESULT_CHARS} characters",
            "partial": serialized[:_MAX_RESULT_CHARS],
        }
    return True, result


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


def _not_run(workspace_id: str, objective: str, reason: str) -> dict:
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
) -> dict:
    """Run the tool loop for one objective; seal the transcript; return the full run record."""
    ws = get_workspace_or_404(session, workspace_id)
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("An objective is required.")
    if len(objective) > _MAX_OBJECTIVE_CHARS:
        raise ValueError(f"Objective must be at most {_MAX_OBJECTIVE_CHARS} characters.")
    max_steps = max(1, min(int(max_steps), _HARD_STEP_CAP))

    external_allowed = ws.external_llm_allowed and ws.data_classification != "restricted"
    if not external_allowed:
        return _not_run(workspace_id, objective, "no_consent")
    if settings.is_mock:
        return _not_run(workspace_id, objective, "mock")
    if not settings.llm_api_key:
        return _not_run(workspace_id, objective, "no_api_key")

    spec = prompt_registry.get("diligence_agent")
    try:
        provider = (provider_factory or LiveProvider)()
    except Exception:
        return _not_run(workspace_id, objective, "error")
    manifest = prompt_registry.manifest("diligence_agent", model=provider.model)

    messages: list[dict] = [{"role": "user", "content": objective}]
    steps: list[dict] = []
    source_parts: list[str] = [objective]
    status = "completed"
    reason = "applied"
    answer: str | None = None

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
            ok, result = _execute_tool(session, workspace_id, name, arguments)
            steps.append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "ok": ok,
                    "result": result if ok else None,
                    "error": None if ok else result,
                }
            )
            if ok:
                source_parts.append(json.dumps(result, default=str))
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
            artifact_metadata={"status": status, "steps_used": len(steps)},
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
    return run_record
