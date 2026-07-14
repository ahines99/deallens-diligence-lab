"""Risk analyst — scans real 10-K risk factors and financial metrics for red flags.

Two evidence sources, both real and cited:
- Text findings: keyword/taxonomy matches against the filing's Item 1A / MD&A chunks, with the
  most on-topic sentence quoted as evidence (claim_type=fact).
- Financial findings: deterministic rule flags over XBRL metrics (claim_type=calculation).
"""
from __future__ import annotations

import re

from src.agents.base import BaseAgent

_SENT_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z(\"'])")


def _best_sentence(text: str, terms: list[str], max_len: int = 380) -> str:
    sentences = _SENT_SPLIT.split(text)
    best, best_hits = text[:max_len], -1
    for s in sentences:
        low = s.lower()
        hits = sum(low.count(t) for t in terms)
        if hits > best_hits and len(s) > 40:
            best, best_hits = s, hits
    best = best.strip()
    if len(best) > max_len:
        best = best[:max_len].rsplit(" ", 1)[0] + "…"
    return best


_REALIZED_MARKERS = (
    "experienced", "occurred", "materially affected", "material adverse", "declined",
    "terminated", "defaulted", "breach occurred", "was unable", "were unable",
)
_CONDITIONAL_MARKERS = (" may ", " might ", " could ", " would ", " risk of ", " if ")


def _severity_from_hits(hits: int, distinct: int, realized: bool = False) -> tuple[str, int]:
    # Frequency in boilerplate is an investigation signal, not proof of occurrence. Text-only
    # scanning cannot produce a critical finding; structured event data must establish that.
    score = min(8 if realized else 6, 2 + distinct + min(hits, 4) // 2 + (2 if realized else 0))
    if score >= 7:
        band = "high"
    elif score >= 4:
        band = "medium"
    else:
        band = "low"
    return band, score


class RiskAnalyst(BaseAgent):
    name = "risk_analyst"
    role = "Scans 10-K risk factors and financials for red flags, each tied to real evidence."

    def scan_text(self, chunks: list, taxonomy: dict, filing_ctx: dict) -> list[dict]:
        """Return text-based findings from filing chunks. `chunks` are DocumentChunk-like objects."""
        # Prefer risk-factor + MD&A sections; fall back to all chunks.
        focus = [c for c in chunks if "Risk Factors" in c.section or "Discussion" in c.section]
        pool = focus or list(chunks)
        findings: list[dict] = []
        for cat in taxonomy["categories"]:
            signals = [s.lower() for s in cat["signals"]]
            best = None  # (score, chunk, matched, hits)
            for c in pool:
                low = c.chunk_text.lower()
                matched = [s for s in signals if s in low]
                if not matched:
                    continue
                hits = sum(low.count(s) for s in matched)
                score = len(matched) * 2 + hits
                if best is None or score > best[0]:
                    best = (score, c, matched, hits)
            if best is None or best[0] < 3:
                continue
            _, chunk, matched, hits = best
            snippet = _best_sentence(chunk.chunk_text, matched)
            normalized_snippet = f" {snippet.lower()} "
            realized = any(marker in normalized_snippet for marker in _REALIZED_MARKERS)
            conditional = any(marker in normalized_snippet for marker in _CONDITIONAL_MARKERS)
            severity, score = _severity_from_hits(hits, len(matched), realized=realized)
            findings.append(
                {
                    "risk_category": cat["slug"],
                    "risk_category_label": cat["label"],
                    "title": f"{cat['label']} disclosure requires diligence",
                    "finding": (
                        f"The {filing_ctx['company']} 10-K discusses {cat['label'].lower()} "
                        f"({hits} related mention(s) in {chunk.section}). Representative disclosure: "
                        f"“{snippet}”"
                    ),
                    "severity": severity,
                    "severity_score": score,
                    "likelihood": "low" if conditional and not realized else "medium",
                    "confidence": round(min(0.72, 0.48 + 0.03 * len(matched)), 3),
                    "workstream_owner": cat["workstream_owner"],
                    "follow_up_question": (
                        f"Quantify the {cat['label'].lower()} exposure and management's mitigation, "
                        f"beyond the risk-factor language."
                    ),
                    "evidence": {
                        "claim": f"{filing_ctx['company']}'s 10-K discusses {cat['label'].lower()}.",
                        "claim_type": "fact",
                        "evidence_text": snippet,
                        "source_name": f"{filing_ctx['company']} 10-K — {chunk.section}",
                        "source_type": "sec_filing",
                        "source_url": filing_ctx.get("url"),
                        "source_date": filing_ctx.get("date"),
                        "source_section": chunk.section,
                        "confidence": round(min(0.72, 0.48 + 0.03 * len(matched)), 3),
                        "agent_name": self.name,
                    },
                }
            )
        return findings

    def financial_flags(self, target, filing_ctx: dict) -> list[dict]:
        """Deterministic red flags from XBRL metrics (claim_type=calculation)."""
        flags: list[dict] = []
        fin = target.financials or {}

        def fy() -> str:
            return (target.fiscal_year_end or "")[:4] or "latest FY"

        def calc_evidence(claim: str, text: str, concept_key: str, conf: float) -> dict:
            src = (fin.get("sources") or {}).get(concept_key) or {}
            has_xbrl_binding = target.data_source.startswith("SEC EDGAR") and bool(
                src.get("concept")
            )
            return {
                "claim": claim,
                "claim_type": "calculation",
                "evidence_text": (
                    text
                    if has_xbrl_binding
                    else f"{text} Inputs are user-supplied and unverified."
                ),
                "source_name": (
                    f"{target.name} FY{fy()} 10-K (XBRL: {src.get('concept', concept_key)})"
                    if has_xbrl_binding
                    else "User-submitted target profile (unverified)"
                ),
                "source_type": "xbrl" if has_xbrl_binding else "user_input",
                "source_url": filing_ctx.get("url") if has_xbrl_binding else None,
                "source_date": (
                    filing_ctx.get("date") if has_xbrl_binding else target.fiscal_year_end
                ),
                "source_section": (
                    "XBRL company facts"
                    if has_xbrl_binding
                    else "User-submitted target profile"
                ),
                "confidence": conf if has_xbrl_binding else min(conf, 0.5),
                "agent_name": "financial_analyst",
            }

        om = target.operating_margin
        if om is not None and om < 0:
            flags.append(_fin_finding(
                "margin_pressure", "Margin pressure", "GAAP operating losses",
                f"{target.name} reported a negative GAAP operating margin of {om:.1%} in FY{fy()}, "
                f"indicating it is not yet profitable on an operating basis.",
                "high" if om < -0.1 else "medium", 7 if om < -0.1 else 6, 0.9, "financial",
                "What is the credible path to GAAP operating profitability, and over what timeframe?",
                calc_evidence(f"FY{fy()} operating margin was {om:.1%}.",
                              f"Operating income / revenue = {om:.1%}.", "operating_income", 0.9),
            ))

        g = target.revenue_growth
        if g is not None and g < 0.08:
            band, score = ("high", 7) if g < 0 else ("medium", 5)
            flags.append(_fin_finding(
                "demand_weakness", "Demand weakness", "Slowing or negative revenue growth",
                f"{target.name}'s revenue growth was {g:.1%} in FY{fy()}, "
                f"{'a contraction' if g < 0 else 'below a typical growth-software threshold'}.",
                band, score, 0.88, "commercial",
                "What is driving the growth trajectory, and what is the forward pipeline/bookings signal?",
                calc_evidence(f"FY{fy()} revenue growth was {g:.1%}.",
                              f"(Revenue - prior revenue) / prior revenue = {g:.1%}.", "revenue", 0.88),
            ))

        ni = target.net_income
        if ni is not None and ni < 0:
            flags.append(_fin_finding(
                "debt_liquidity", "Debt & liquidity", "Net losses on a GAAP basis",
                f"{target.name} reported a GAAP net loss of ${ni/1e6:,.0f}M in FY{fy()}; "
                f"assess cash runway and reliance on external financing.",
                "medium", 5, 0.88, "financial",
                "What is the cash runway at the current burn, and what financing is contemplated?",
                calc_evidence(f"FY{fy()} net income was ${ni/1e6:,.0f}M.",
                              f"Reported NetIncomeLoss = ${ni/1e6:,.0f}M.", "net_income", 0.88),
            ))

        debt, cash = target.total_debt, target.cash
        if debt and cash and debt > 2 * cash:
            flags.append(_fin_finding(
                "debt_liquidity", "Debt & liquidity", "Debt materially exceeds cash",
                f"{target.name} carries ${debt/1e6:,.0f}M of debt against ${cash/1e6:,.0f}M cash "
                f"({debt/cash:.1f}x), a leverage/refinancing consideration for a buyout structure.",
                "medium", 5, 0.85, "financial",
                "What is the maturity schedule and covenant package, and what leverage can EBITDA support?",
                calc_evidence(f"Debt/cash is {debt/cash:.1f}x.",
                              f"LongTermDebt ${debt/1e6:,.0f}M / cash ${cash/1e6:,.0f}M = {debt/cash:.1f}x.",
                              "total_debt", 0.85),
            ))

        gm = target.gross_margin
        if gm is not None and gm < 0.5:
            flags.append(_fin_finding(
                "margin_pressure", "Margin pressure", "Gross margin below software norms",
                f"{target.name}'s gross margin of {gm:.1%} is below typical software levels, "
                f"which constrains operating leverage and valuation framing.",
                "low", 3, 0.8, "financial",
                "What is the cost-of-revenue mix, and is there a credible gross-margin expansion path?",
                calc_evidence(f"FY{fy()} gross margin was {gm:.1%}.",
                              f"Gross profit / revenue = {gm:.1%}.", "gross_profit", 0.8),
            ))
        return flags


    def govcon_flags(self, profile) -> list[dict]:
        """Red flags from a USAspending federal-contract profile (Release 0.5)."""
        flags: list[dict] = []
        if not profile:
            return flags
        total = getattr(profile, "total_obligations", 0) or 0
        if total <= 0:
            return flags
        name = profile.recipient_name

        def gov_evidence(claim: str, text: str, conf: float) -> dict:
            return {
                "claim": claim,
                "claim_type": "fact",
                "evidence_text": text,
                "source_name": "USAspending.gov — federal contract awards",
                "source_type": "usaspending",
                "source_url": "https://www.usaspending.gov/",
                "source_date": None,
                "source_section": "Federal award history (contracts)",
                "confidence": conf,
                "agent_name": self.name,
            }

        pct = profile.top_agency_pct
        agency = profile.top_agency
        if pct is not None and pct >= 0.5:
            sev, score = ("high", 7) if pct >= 0.65 else ("medium", 6)
            flags.append(_fin_finding(
                "govcon_risk", "GovCon / contract risk",
                f"Federal revenue concentrated in {agency}",
                f"{name} derives {pct:.0%} of its ${total/1e6:,.0f}M in federal contract obligations from "
                f"{agency} — single-agency concentration exposes the business to that agency's budget, "
                f"appropriations, and recompete cycle.",
                sev, score, 0.8, "govcon",
                f"What share of total revenue is federal, and how exposed is {agency} funding to appropriations risk?",
                gov_evidence(
                    f"{pct:.0%} of {name}'s federal contract obligations are from {agency}.",
                    f"USAspending: {agency} = {pct:.0%} of ${total/1e6:,.0f}M total contract obligations.",
                    0.8,
                ),
            ))

        rc = profile.recompete or {}
        if rc.get("count", 0) > 0:
            val = rc.get("value", 0) or 0
            heavy = total and val > 0.2 * total
            sev, score = ("high", 7) if heavy else ("medium", 5)
            flags.append(_fin_finding(
                "govcon_risk", "GovCon / contract risk",
                "Major awards up for recompete within 24 months",
                f"{rc['count']} of {name}'s largest awards (~${val/1e6:,.0f}M) have periods of performance "
                f"ending within 24 months and face recompete; loss of incumbency would directly reduce revenue.",
                sev, score, 0.78, "govcon",
                "What is the recompete calendar, historical recompete win rate, and the incumbency advantage?",
                gov_evidence(
                    f"{rc['count']} major awards (~${val/1e6:,.0f}M) are up for recompete within 24 months.",
                    f"USAspending periods of performance: {rc['count']} top awards end within 24 months.",
                    0.78,
                ),
            ))
        return flags


def _fin_finding(cat, label, title, finding, severity, score, conf, ws, followup, evidence):
    return {
        "risk_category": cat,
        "risk_category_label": label,
        "title": title,
        "finding": finding,
        "severity": severity,
        "severity_score": score,
        "likelihood": "high" if score >= 6 else "medium",
        "confidence": conf,
        "workstream_owner": ws,
        "follow_up_question": followup,
        "evidence": evidence,
    }
