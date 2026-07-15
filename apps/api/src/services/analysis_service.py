"""Full diligence analysis orchestrator.

Given a workspace whose target has been ingested from EDGAR (financials + 10-K chunks), this
(re)builds the entire pack deterministically and idempotently: evidence, risk findings, questions,
plan, IC memo, and red-team/bear-case. Every material claim gets a real Evidence row so citations
always resolve. Optional live-LLM polish re-voices the memos without changing numbers.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.agents.diligence_lead import DiligenceLead
from src.agents.ic_memo_writer import ICMemoWriter
from src.agents.llm_provider import polish_markdown
from src.agents.red_team_reviewer import RedTeamReviewer
from src.agents.risk_analyst import RiskAnalyst
from src.db.base import now_utc
from src.models import (
    DiligencePlan,
    DiligenceQuestion,
    Filing,
    Memo,
    RedTeamReport,
    RiskFinding,
    Target,
)
from src.models.underwriting_data import AnalysisRun, ArtifactVersion
from src.seed import loader
from src.services import evidence_service
from src.services.common import NotFound, get_workspace_or_404, touch_status

logger = logging.getLogger("deallens.analysis")

# (target attr, label, claim_type, source-concept key)
_FIN_METRICS = [
    ("revenue", "Revenue", "fact", "revenue"),
    ("revenue_growth", "Revenue growth", "calculation", "revenue"),
    ("gross_margin", "Gross margin", "calculation", "gross_profit"),
    ("operating_margin", "Operating margin", "calculation", "operating_income"),
    ("net_income", "Net income", "fact", "net_income"),
    ("net_margin", "Net margin", "calculation", "net_income"),
    ("rnd_pct", "R&D % of revenue", "calculation", "rnd"),
    ("rule_of_40", "Rule of 40", "calculation", "revenue"),
    ("cash", "Cash", "fact", "cash"),
    ("total_debt", "Total debt", "fact", "total_debt"),
]
_PCT = {"revenue_growth", "gross_margin", "operating_margin", "net_margin", "rnd_pct", "rule_of_40"}


def _fmt(attr: str, v: float) -> str:
    if attr in _PCT:
        return f"{v*100:.1f}%"
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:,.1f}B"
    if a >= 1e6:
        return f"${v/1e6:,.0f}M"
    return f"${v:,.0f}"


def _latest_10k(session: Session, workspace_id: str) -> Filing | None:
    return session.scalar(
        select(Filing)
        .where(Filing.workspace_id == workspace_id, Filing.form_type == "10-K")
        .order_by(Filing.filing_date.desc())
    )


def run_full_analysis(
    session: Session, workspace_id: str, heartbeat: Callable[[], None] | None = None
) -> None:
    """Run and seal the projection atomically; any failure restores the prior current view.

    ``heartbeat`` (optional) is pinged between the long analysis stages so a durable job
    runner can prove liveness through the LLM-bound phases (memo, red team) and avoid a
    stale-recovery double-build.
    """
    try:
        _run_full_analysis(session, workspace_id, heartbeat)
    except Exception:
        session.rollback()
        raise


def _run_full_analysis(
    session: Session, workspace_id: str, heartbeat: Callable[[], None] | None = None
) -> None:
    def beat() -> None:
        if heartbeat is not None:
            heartbeat()

    ws = get_workspace_or_404(session, workspace_id)
    target: Target | None = session.scalar(select(Target).where(Target.workspace_id == workspace_id))
    if target is None:
        raise NotFound("No target ingested for this workspace. Create it with a ticker first.")

    # Replace current-state views, while retaining prior Evidence rows. Stable evidence refs remain
    # resolvable for frozen artifact versions and newly generated claims receive new refs.
    session.execute(delete(RiskFinding).where(RiskFinding.workspace_id == workspace_id))
    session.execute(delete(DiligenceQuestion).where(DiligenceQuestion.workspace_id == workspace_id))
    session.execute(delete(DiligencePlan).where(DiligencePlan.workspace_id == workspace_id))
    session.execute(delete(Memo).where(Memo.workspace_id == workspace_id))
    session.execute(delete(RedTeamReport).where(RedTeamReport.workspace_id == workspace_id))
    session.flush()

    tenk = _latest_10k(session, workspace_id)
    filing_ctx = {
        "company": target.name,
        "url": tenk.document_url if tenk else None,
        "date": tenk.filing_date if tenk else target.fiscal_year_end,
    }

    # 1) Financial evidence (facts + calculations) -> ref map.
    fin_refs: dict[str, str] = {}
    fin = target.financials or {}
    sources = fin.get("sources") or {}
    sec_sourced_target = target.data_source.startswith("SEC EDGAR")
    fy = (target.fiscal_year_end or "")[:4] or "latest FY"
    for attr, label, claim_type, concept_key in _FIN_METRICS:
        v = getattr(target, attr, None)
        if v is None:
            continue
        src = sources.get(concept_key) or {}
        concept = src.get("concept", concept_key)
        has_xbrl_binding = sec_sourced_target and bool(src.get("concept"))
        effective_claim_type = claim_type if has_xbrl_binding else "assumption"
        ev = evidence_service.create(
            session,
            workspace_id,
            claim=f"{target.name} FY{fy} {label.lower()} was {_fmt(attr, v)}.",
            claim_type=effective_claim_type,
            source_name=(
                f"{target.name} FY{fy} 10-K (XBRL: {concept})"
                if has_xbrl_binding
                else "User-submitted target profile (unverified)"
            ),
            source_type="xbrl" if has_xbrl_binding else "user_input",
            evidence_text=(
                f"{label}: {_fmt(attr, v)} (SEC XBRL company facts, concept {concept})."
                if has_xbrl_binding
                else f"{label}: {_fmt(attr, v)} (unverified value supplied by the user)."
            ),
            confidence=(0.95 if claim_type == "fact" else 0.9) if has_xbrl_binding else 0.5,
            agent_name="financial_analyst",
            source_url=filing_ctx["url"] if has_xbrl_binding else None,
            source_date=filing_ctx["date"] if has_xbrl_binding else target.fiscal_year_end,
            source_section=(
                "XBRL company facts" if has_xbrl_binding else "User-submitted target profile"
            ),
        )
        fin_refs[attr] = ev.ref

    # Multi-year revenue CAGR (calculation) if trend data is present.
    trends = fin.get("trends") or {}
    cagr = trends.get("revenue_cagr")
    if cagr is not None and trends.get("years"):
        yrs = trends["years"]
        ev = evidence_service.create(
            session,
            workspace_id,
            claim=f"{target.name} revenue CAGR was {cagr*100:.1f}% over FY{yrs[0]}-FY{yrs[-1]}.",
            claim_type="calculation",
            source_name=f"{target.name} 10-K history (XBRL revenue, FY{yrs[0]}-{yrs[-1]})",
            source_type="xbrl",
            evidence_text=f"{len(yrs)}-year revenue CAGR = {cagr*100:.1f}% (FY{yrs[0]} to FY{yrs[-1]}).",
            confidence=0.9,
            agent_name="financial_analyst",
            source_url=filing_ctx["url"],
            source_date=filing_ctx["date"],
            source_section="XBRL company facts (multi-year)",
        )
        fin_refs["revenue_cagr"] = ev.ref

    # 2) Risk findings (text scan + financial flags + GovCon flags) with evidence.
    from src.services import govcon_service

    analyst = RiskAnalyst()
    taxonomy = loader.risk_taxonomy()
    chunks = _chunks(session, workspace_id)
    govcon = govcon_service.get_optional(session, workspace_id)
    raw_findings = (
        analyst.scan_text(chunks, taxonomy, filing_ctx)
        + analyst.financial_flags(target, filing_ctx)
        + analyst.govcon_flags(govcon)
    )
    # Wave 2 extension flags (forensics + SEC feeds). Each is best-effort: a failure
    # (e.g. missing forensic_inputs, SEC unreachable) must never break analysis — but it is
    # recorded as a degraded source so the sealed run never infers "clean" from a failure.
    import importlib

    degraded_sources: list[str] = []
    for mod_name in ("forensics_service", "sec_feeds_service"):
        try:
            module = importlib.import_module(f"src.services.{mod_name}")
            raw_findings += module.risk_flags(session, workspace_id)
        except Exception as exc:  # noqa: BLE001 — extensions are optional but their failure is not silent
            logger.warning("Risk-flag source '%s' unavailable during analysis: %s", mod_name, exc)
            degraded_sources.append(mod_name)
    raw_findings.sort(key=lambda f: f["severity_score"], reverse=True)
    beat()

    for f in raw_findings:
        ev = evidence_service.create(session, workspace_id, **f["evidence"])
        f["evidence_ref"] = ev.ref
        session.add(
            RiskFinding(
                workspace_id=workspace_id,
                risk_category=f["risk_category"],
                risk_category_label=f["risk_category_label"],
                title=f["title"],
                finding=f["finding"],
                severity=f["severity"],
                severity_score=f["severity_score"],
                likelihood=f["likelihood"],
                confidence=f["confidence"],
                evidence_ref=ev.ref,
                follow_up_question=f["follow_up_question"],
                workstream_owner=f["workstream_owner"],
            )
        )
    session.flush()

    # 3) Plan + questions.
    lead = DiligenceLead()
    plan_data = lead.build_plan(target, ws.investment_question, raw_findings)
    session.add(
        DiligencePlan(
            workspace_id=workspace_id,
            investment_question=ws.investment_question,
            summary=plan_data["summary"],
            workstreams=plan_data["workstreams"],
        )
    )
    question_data = lead.build_questions(target, raw_findings)
    for q in question_data:
        session.add(
            DiligenceQuestion(
                workspace_id=workspace_id,
                workstream=q["workstream"],
                workstream_label=q["workstream_label"],
                question=q["question"],
                rationale=q["rationale"],
                priority=q["priority"],
                evidence_ref=q.get("evidence_ref"),
            )
        )
    session.flush()

    # 4) Benchmark (from comps if present) for the memo.
    from src.services import financial_benchmark_service

    try:
        benchmark = financial_benchmark_service.compute_benchmark(session, workspace_id)
    except NotFound:
        benchmark = None

    ctx = {
        "target": target,
        "fin_refs": fin_refs,
        "findings": raw_findings,
        "benchmark": benchmark,
        "filing": filing_ctx,
        "investment_question": ws.investment_question,
        "govcon": govcon,
        "trends": trends,
    }

    # 5) IC memo.
    beat()
    external_llm_allowed = ws.external_llm_allowed and ws.data_classification != "restricted"
    memo_polish = polish_markdown(
        ICMemoWriter().draft(ctx), external_allowed=external_llm_allowed
    )
    memo_md = memo_polish.text
    session.add(
        Memo(
            workspace_id=workspace_id,
            memo_type="ic_memo",
            title=f"Investment Committee Memo — {target.name}",
            markdown_content=memo_md,
        )
    )

    # 6) Red-team / bear-case.
    beat()
    rt = RedTeamReviewer().build(ctx)
    bear_polish = polish_markdown(
        rt["bear_case_markdown"], external_allowed=external_llm_allowed
    )
    bear_md = bear_polish.text
    session.add(
        RedTeamReport(
            workspace_id=workspace_id,
            bear_case_markdown=bear_md,
            summary=rt["summary"],
            unsupported_claims=rt["unsupported_claims"],
            missing_evidence=rt["missing_evidence"],
            high_priority_questions=rt["high_priority_questions"],
        )
    )
    session.add(
        Memo(
            workspace_id=workspace_id,
            memo_type="bear_case",
            title=f"Bear-Case / Red-Team Memo — {target.name}",
            markdown_content=bear_md,
        )
    )

    touch_status(ws, "complete")

    # Seal the reproducible run and artifact in the same transaction as the current-state
    # projection. A failed seal therefore cannot leave deleted prior projections or an unversioned
    # memo behind, while prior ArtifactVersion rows remain immutable and resolvable.
    from src.services import underwriting_data_service

    input_manifest = {
        "workspace_id": workspace_id,
        "target_id": target.id,
        "target_updated_at": target.updated_at.isoformat() if target.updated_at else None,
        "filing_id": tenk.id if tenk else None,
        "filing_accession": tenk.accession_number if tenk else None,
        "financial_sources": sources,
    }
    # Provenance is honest: a run is only "deterministic" when no external LLM touched it.
    llm_applied = memo_polish.applied or bear_polish.applied
    output_summary = {
        "risk_count": len(raw_findings),
        "question_count": len(question_data),
        "financial_evidence_count": len(fin_refs),
        "artifacts": ["plan", "ic_memo", "bear_case"],
        # Explicit source status: extension failures are recorded, never inferred clean.
        "degraded_sources": degraded_sources,
        "llm_polished": llm_applied,
        "llm_polish_outcome": {
            "ic_memo": memo_polish.reason,
            "bear_case": bear_polish.reason,
        },
    }
    latest_run = session.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.workspace_id == workspace_id,
            AnalysisRun.run_type == "full_diligence",
        )
        .order_by(AnalysisRun.version.desc())
    )
    completed_at = now_utc()
    run = AnalysisRun(
        workspace_id=workspace_id,
        run_type="full_diligence",
        version=(latest_run.version + 1) if latest_run else 1,
        supersedes_id=latest_run.id if latest_run else None,
        status="succeeded",
        input_hash=underwriting_data_service.content_hash(input_manifest),
        content_hash=underwriting_data_service.content_hash(output_summary),
        source_snapshot_ids=[],
        input_manifest=input_manifest,
        output_summary=output_summary,
        model_version=(
            (memo_polish.model or bear_polish.model or "external-llm")
            if llm_applied
            else "deterministic-wave3"
        ),
        prompt_version=(
            (memo_polish.prompt_version or bear_polish.prompt_version) if llm_applied else None
        ),
        code_version="wave3",
        error_message=None,
        created_by="analysis_service",
        started_at=completed_at,
        completed_at=completed_at,
    )
    session.add(run)
    session.flush()
    content_json = {
        "plan": plan_data,
        "risk_findings": raw_findings,
        "questions": question_data,
        "ic_memo_markdown": memo_md,
        "red_team": rt,
        "financial_evidence_refs": fin_refs,
    }
    latest_artifact = session.scalar(
        select(ArtifactVersion)
        .where(
            ArtifactVersion.workspace_id == workspace_id,
            ArtifactVersion.artifact_type == "diligence_pack",
        )
        .order_by(ArtifactVersion.version.desc())
    )
    artifact = ArtifactVersion(
        workspace_id=workspace_id,
        artifact_type="diligence_pack",
        version=(latest_artifact.version + 1) if latest_artifact else 1,
        supersedes_id=latest_artifact.id if latest_artifact else None,
        analysis_run_id=run.id,
        source_snapshot_ids=[],
        input_hash=underwriting_data_service.content_hash(input_manifest),
        content_hash=underwriting_data_service.content_hash(content_json),
        content_json=content_json,
        content_text=None,
        file_uri=None,
        artifact_metadata={
            "target_name": target.name,
            "filing_accession": tenk.accession_number if tenk else None,
        },
        created_by="analysis_service",
    )
    session.add(artifact)
    session.commit()


def _chunks(session: Session, workspace_id: str):
    from src.models import DocumentChunk

    return session.scalars(
        select(DocumentChunk).where(DocumentChunk.workspace_id == workspace_id)
    ).all()
