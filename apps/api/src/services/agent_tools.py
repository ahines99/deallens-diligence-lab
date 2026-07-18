"""G57/G60 — the diligence agent's governed tool registry.

Split from ``agent_service`` so Theme-I features can evolve tools and loop independently.
Every handler takes (session, workspace_id, arguments) and returns a JSON-safe dict. Handlers
are read-only or pure compute by construction — a WRITE-capable tool may only PROPOSE into a
four-eyes queue (never approve, never mutate governed records directly); see the module
docstring in ``agent_service`` before adding one.
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError
from sqlalchemy import select

from src.models import RiskFinding, Target
from src.models.underwriting_model import UnderwritingCaseVersion
from src.services import evidence_service, filings_qa_service, retrieval_service
from src.services.common import get_workspace_or_404

logger = logging.getLogger("deallens.agent")

# Serialized tool results larger than this are truncated with an explicit marker: the model sees
# less, never something fabricated.
MAX_RESULT_CHARS = 6_000

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
    if len(serialized) > MAX_RESULT_CHARS:
        return True, {
            "truncated": True,
            "note": f"result truncated to {MAX_RESULT_CHARS} characters",
            "partial": serialized[:MAX_RESULT_CHARS],
        }
    return True, result
