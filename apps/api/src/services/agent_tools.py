"""G57/G60 — the diligence agent's governed tool registry.

Split from ``agent_service`` so Theme-I features can evolve tools and loop independently.
Every handler takes (session, workspace_id, arguments) and returns a JSON-safe dict. Handlers
are read-only, pure compute, or PROPOSE-only by construction. G60 adds the two proposal
tools: the agent may place a QoE adjustment or a structured claim INTO an existing four-eyes
queue under the distinguishable automation identity ``agent:diligence`` — it can never decide
one, and neither can any other automation identity, because the queues' existing checks
already reject automation as a decider: the proposer!=decider rule (``decide_qoe_adjustment``,
``review_claim``), the trusted-service reviewer ban (``_require_human_reviewer``), and the
router-level ``HumanDeciderDep``. A HUMAN deciding an agent proposal is possible and
unchanged. Proposal tool calls and returned record ids flow into the loop transcript, so the
sealed ``agent_run`` artifact (which binds the prompt/model manifest) remains the audit trail
tying each proposal to the run that made it.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import get_args

from pydantic import ValidationError
from sqlalchemy import select

from src.agents.citation_auditor import EV_REF_PATTERN
from src.models import RiskFinding, Target
from src.models.underwriting_model import UnderwritingCaseVersion
from src.services import evidence_service, filings_qa_service, retrieval_service
from src.services.common import get_workspace_or_404

logger = logging.getLogger("deallens.agent")

# Serialized tool results larger than this are truncated with an explicit marker: the model sees
# less, never something fabricated.
MAX_RESULT_CHARS = 6_000

# G60: the distinguishable proposer identity every agent proposal carries. It is deliberately a
# value no human session can hold (actor ids come from verified principals), so the four-eyes
# proposer!=decider checks make an agent proposal undecidable by the same identity, and the
# human-reviewer requirements make it undecidable by ANY automation identity.
AGENT_ACTOR_ID = "agent:diligence"
# Engine provenance on agent-proposed claims, alongside G53's rules-/llm- extraction versions.
AGENT_CLAIM_EXTRACTION_VERSION = "agent-proposed-v1"
_QOE_BRIDGE_LAYERS = ("management", "sponsor")

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
    wanted: set[str] = set()
    for ref in refs:
        cleaned = str(ref).strip()
        # Shape-validate BEFORE any lookup or echo, against the SAME pattern the grounding
        # gate's auditor uses: only EV-### strings may flow through this tool at all, so
        # free-text (a fabricated figure, prose, another workspace's ids) can never ride
        # along as a "reference to resolve".
        if not EV_REF_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"refs must be EV-### evidence references; {cleaned[:40]!r} is not one"
            )
        wanted.add(cleaned)
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


def _tool_propose_qoe_adjustment(session, workspace_id: str, arguments: dict) -> dict:
    """PROPOSE a QoE adjustment into the existing four-eyes queue — never decide one.

    The proposal runs through the same ``create_qoe_adjustment`` path a human proposer uses:
    it lands with status "proposed" and ``created_by=AGENT_ACTOR_ID``, surfaces in the review
    inbox for every human EXCEPT its proposer, and the proposer!=decider rule plus the
    human-decider requirement on the decide route mean no automation can ever approve it.
    """
    from src.schemas.underwriting_data import QoEAdjustmentCreate
    from src.services import underwriting_data_service

    bridge_layer = arguments.get("bridge_layer")
    if bridge_layer not in _QOE_BRIDGE_LAYERS:
        raise ValueError(f"bridge_layer must be one of {list(_QOE_BRIDGE_LAYERS)}")
    description = str(arguments.get("description") or "").strip()
    if not description:
        raise ValueError("description is required")
    category = str(arguments.get("category") or "").strip()
    if not category:
        raise ValueError("category is required")
    amount = arguments.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValueError("amount must be a number")
    try:
        amount_decimal = Decimal(str(amount))
    except InvalidOperation as exc:  # pragma: no cover - repr of a float always parses
        raise ValueError("amount must be a number") from exc
    source_note = str(arguments.get("source_note") or "").strip()
    if source_note:
        # The record itself carries the agent's source context; the sealed run transcript
        # (with its prompt/model manifest) remains the full provenance trail.
        description = f"{description}\n\nAgent source note: {source_note}"
    try:
        data = QoEAdjustmentCreate(
            period_end=arguments.get("period_end"),
            bridge_layer=bridge_layer,
            title=" ".join(description.split())[:240],
            description=description,
            category=category,
            amount=amount_decimal,
            evidence_ref=arguments.get("evidence_ref") or None,
            created_by=AGENT_ACTOR_ID,
        )
    except ValidationError as exc:
        raise ValueError(f"invalid QoE proposal: {exc}") from exc
    adjustment = underwriting_data_service.create_qoe_adjustment(session, workspace_id, data)
    return {
        "proposed": True,
        "adjustment_id": adjustment.id,
        "status": adjustment.status,
        "created_by": adjustment.created_by,
    }


def _tool_propose_claim(session, workspace_id: str, arguments: dict) -> dict:
    """PROPOSE one structured claim on the linked deal, gated by the G53 verifier.

    Only deal-linked workspaces have a data room to claim against. The quote must appear
    verbatim (whitespace-normalized only) in a latest-version data-room chunk, and the claimed
    value_text/value_number must be visible inside that quote (digit-boundary rule for
    numbers) — the same deterministic verification G53 applies to LLM extraction, reusing its
    helpers. Anything unverifiable is a tool error and NOTHING is minted. A verified claim
    lands "unreviewed" with ``created_by_actor_id=AGENT_ACTOR_ID`` for human four-eyes review.
    """
    from src.db.base import new_uuid
    from src.models.deal_intelligence import StructuredClaim
    from src.models.deal_workflow import Deal
    from src.schemas.deal_intelligence import ClaimCategory
    from src.services import deal_intelligence_service as intelligence

    deal = session.scalar(select(Deal).where(Deal.workspace_id == workspace_id))
    if deal is None:
        raise ValueError(
            "this workspace is not linked to a deal, so it has no data room to claim against; "
            "propose_claim is only available on deal-linked workspaces"
        )
    allowed_categories = set(get_args(ClaimCategory))
    category = str(arguments.get("category") or "").strip()
    if category not in allowed_categories:
        raise ValueError(f"category must be one of {sorted(allowed_categories)}")
    field_name = str(arguments.get("field_name") or "").strip()
    if not field_name or len(field_name) > 100:
        raise ValueError("field_name must be 1-100 characters")
    value_text = str(arguments.get("value_text") or "").strip()
    if not value_text:
        raise ValueError("value_text is required")
    quote = str(arguments.get("quote") or "").strip()
    if not quote:
        raise ValueError("quote is required")
    value_number = arguments.get("value_number")
    if value_number is not None:
        if isinstance(value_number, bool) or not isinstance(value_number, (int, float)):
            raise ValueError("value_number must be a number")
        value_number = float(value_number)

    hint = str(arguments.get("chunk_hint") or "").strip().casefold()
    documents = intelligence.list_documents(session, deal.id, latest_only=True)
    if hint:
        documents.sort(
            key=lambda doc: hint not in f"{doc.id} {doc.filename} {doc.title}".casefold()
        )
    located = None
    for document in documents:
        for chunk in intelligence.list_chunks(session, document.id):
            span = intelligence.verbatim_span(chunk.text, quote)
            if span is not None:
                located = (document, chunk, span)
                break
        if located:
            break
    if located is None:
        raise ValueError(
            "quote_not_verbatim: the quote does not appear verbatim (whitespace-normalized) in "
            "any current data-room chunk; only text present in the deal's documents can back a "
            "proposed claim"
        )
    document, chunk, (start, end) = located
    quoted = chunk.text[start:end]
    collapse = intelligence.whitespace_collapsed
    if collapse(value_text) not in collapse(quoted):
        raise ValueError("value_text_not_in_quote: value_text must appear inside the quote")
    if value_number is not None:
        mismatch = intelligence.value_number_mismatch(value_number, value_text, quoted)
        if mismatch is not None:
            raise ValueError(
                f"{mismatch}: value_number must be the number stated in value_text and must "
                "appear inside the quote on digit boundaries"
            )

    # Same-span dedupe mirrors G53's signature reuse: an identical revision-1 claim is
    # returned, never re-minted into a second queue item.
    for candidate in session.scalars(
        select(StructuredClaim).where(
            StructuredClaim.chunk_id == chunk.id,
            StructuredClaim.category == category,
            StructuredClaim.field_name == field_name,
            StructuredClaim.revision == 1,
        )
    ):
        span_json = candidate.source_span or {}
        if span_json.get("start") == start and span_json.get("end") == end:
            return {
                "proposed": False,
                "claim_id": candidate.id,
                "review_status": candidate.review_status,
                "note": "an identical claim already exists for this exact span",
            }

    unit = arguments.get("unit")
    period = arguments.get("period")
    claim = StructuredClaim(
        deal_id=deal.id,
        logical_claim_id=new_uuid(),
        revision=1,
        document_id=document.id,
        chunk_id=chunk.id,
        category=category,
        field_name=field_name,
        value_text=value_text,
        value_number=value_number,
        unit=intelligence.bounded_metadata(str(unit), 40) if unit is not None else None,
        period=intelligence.bounded_metadata(str(period), 40) if period is not None else None,
        currency=None,
        # Same fixed confidence rationale as G53: a deterministic verifier bound the quote and
        # value to a real chunk; the score is not a model-reported probability.
        confidence=intelligence.LLM_CLAIM_CONFIDENCE,
        source_locator=deepcopy(chunk.locator),
        source_span={"start": start, "end": end, "text": quoted},
        review_status="unreviewed",
        extraction_version=AGENT_CLAIM_EXTRACTION_VERSION,
        created_by_actor_id=AGENT_ACTOR_ID,
    )
    session.add(claim)
    session.commit()
    return {
        "proposed": True,
        "claim_id": claim.id,
        "logical_claim_id": claim.logical_claim_id,
        "review_status": claim.review_status,
        "document_id": document.id,
        "chunk_id": chunk.id,
        "locator": claim.source_locator,
        "created_by": AGENT_ACTOR_ID,
        "extraction_version": AGENT_CLAIM_EXTRACTION_VERSION,
    }


# Each registry entry is (description, input_schema, handler, argument_echo_fields). The LAST
# member is a REQUIRED declaration: result fields that merely REFLECT the model's own arguments
# rather than data the tool produced. The model still sees them (useful feedback, sealed in the
# transcript), but the grounding gate must never treat them as evidence — an ungrounded figure
# or EV-### ref passed as an argument would otherwise launder itself into the gate's source
# (e.g. a fabricated ref echoed back through ``get_evidence.unresolved``). Making the
# declaration a positional tuple member means a new tool CANNOT be registered without deciding
# it — an empty frozenset is an explicit "echoes nothing" statement, not an omission.
_TOOLS: dict[str, tuple[str, dict, callable, frozenset[str]]] = {
    "get_workspace_overview": (
        "Workspace, target, headline financials, and recent revenue/margin trend rows.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_get_workspace_overview,
        frozenset(),
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
        frozenset(),
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
        frozenset(),
    ),
    "list_risk_findings": (
        "Current risk findings with severities and EV-### evidence references.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_list_risk_findings,
        frozenset(),
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
        frozenset({"unresolved"}),
    ),
    "list_underwriting_cases": (
        "Latest saved underwriting case versions with headline IRR/MoIC.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_list_underwriting_cases,
        frozenset(),
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
        frozenset(),
    ),
    "propose_qoe_adjustment": (
        "PROPOSE a QoE adjustment into the four-eyes review queue as agent:diligence (it lands "
        "with status 'proposed'). You can never approve or reject one — a distinct HUMAN "
        "reviewer decides it in the review inbox.",
        {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "description": {"type": "string"},
                "amount": {"type": "number"},
                "period_end": {
                    "type": "string",
                    "description": "ISO date the adjustment period ends, e.g. 2025-12-31",
                },
                "bridge_layer": {"type": "string", "enum": ["management", "sponsor"]},
                "evidence_ref": {"type": "string"},
                "source_note": {"type": "string"},
            },
            "required": ["category", "description", "amount", "period_end", "bridge_layer"],
            "additionalProperties": False,
        },
        _tool_propose_qoe_adjustment,
        frozenset(),
    ),
    "propose_claim": (
        "PROPOSE one structured claim on the linked deal from a VERBATIM data-room quote. The "
        "quote and value are verified deterministically against the real chunks; a verified "
        "claim is minted 'unreviewed' as agent:diligence for human four-eyes review. "
        "Deal-linked workspaces only; you can never approve a claim.",
        {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["debt_term", "customer", "contract", "kpi", "qoe_candidate"],
                },
                "field_name": {"type": "string"},
                "value_text": {
                    "type": "string",
                    "description": "the claimed value, copied verbatim from inside the quote",
                },
                "value_number": {"type": "number"},
                "unit": {"type": "string"},
                "period": {"type": "string"},
                "quote": {
                    "type": "string",
                    "description": "verbatim supporting text from a data-room document chunk",
                },
                "chunk_hint": {
                    "type": "string",
                    "description": "optional document filename/title/id fragment to search first",
                },
            },
            "required": ["category", "field_name", "value_text", "quote"],
            "additionalProperties": False,
        },
        _tool_propose_claim,
        frozenset(),
    ),
}


def tool_definitions() -> list[dict]:
    """Anthropic-format tool declarations for the loop (and the contract tests)."""
    return [
        {"name": name, "description": description, "input_schema": schema}
        for name, (description, schema, _handler, _echo_fields) in _TOOLS.items()
    ]


def grounding_projection(name: str, result: dict | str) -> dict | str:
    """The grounding-safe view of one successful tool result.

    Everything the tool PRODUCED, nothing that echoes what the model ASKED (per the registry's
    per-tool echo-field declaration) — only this projection may feed the grounding gate's
    source text.
    """
    spec = _TOOLS.get(name)
    echoed = spec[3] if spec is not None else frozenset()
    if not echoed or not isinstance(result, dict):
        return result
    return {key: value for key, value in result.items() if key not in echoed}


def _execute_tool(
    session, workspace_id: str, name: str, arguments: dict
) -> tuple[bool, dict | str, str]:
    """Run one governed tool; (ok, result-or-error, grounding_source_text).

    Errors go back to the model, never raise, and contribute NOTHING to the grounding source.
    ``grounding_source_text`` serializes the argument-echo-free projection, computed from the
    UNtruncated result (so a truncated result's ``partial`` blob, echo fields included, never
    reaches the gate) but capped at the same size the model sees — the gate must not treat
    evidence the model could never have read as grounding for its prose.
    """
    spec = _TOOLS.get(name)
    if spec is None:
        return False, f"unknown tool: {name!r}", ""
    _description, _schema, handler, echo_fields = spec
    try:
        result = handler(session, workspace_id, arguments or {})
    except ValueError as exc:
        return False, str(exc), ""
    except Exception as exc:  # noqa: BLE001 - a tool crash must not kill the sealed run
        logger.warning("Agent tool '%s' failed: %s", name, exc)
        return False, f"tool failed: {exc}", ""
    serialized = json.dumps(result, default=str)
    projection = grounding_projection(name, result)
    grounding_text = (
        serialized if projection is result else json.dumps(projection, default=str)
    )[:MAX_RESULT_CHARS]
    if len(serialized) > MAX_RESULT_CHARS:
        return True, {
            "truncated": True,
            "note": f"result truncated to {MAX_RESULT_CHARS} characters",
            "partial": serialized[:MAX_RESULT_CHARS],
        }, grounding_text
    return True, result, grounding_text
