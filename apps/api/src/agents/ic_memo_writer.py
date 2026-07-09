"""IC memo writer — drafts the investment committee memo from real data (deterministic).

Every figure is a real XBRL value or a real filing disclosure and cites its evidence ref. The
optional live-LLM path (llm_provider) may re-voice this prose but does not change the numbers.
"""
from __future__ import annotations

from src.agents.base import BaseAgent


def _usd(v):
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:,.1f}B"
    if a >= 1e6:
        return f"${v/1e6:,.0f}M"
    return f"${v:,.0f}"


def _pct(v):
    return "n/a" if v is None else f"{v*100:.1f}%"


def _cite(ref):
    return f" [{ref}]" if ref else ""


class ICMemoWriter(BaseAgent):
    name = "ic_memo_writer"
    role = "Drafts the IC memo narrative from real financials and findings; cites every claim."

    def draft(self, ctx: dict) -> str:
        t = ctx["target"]
        r = ctx["fin_refs"]
        findings = ctx["findings"]
        bench = ctx.get("benchmark")
        iq = ctx.get("investment_question") or ""
        filing = ctx.get("filing") or {}

        top = sorted(findings, key=lambda f: f["severity_score"], reverse=True)[:6]
        high = [f for f in findings if f["severity"] in ("high", "critical")]

        lines: list[str] = []
        lines.append(f"# Investment Committee Memo — {t.name} ({t.ticker or 'private'}) (DRAFT)")
        lines.append("")
        lines.append(
            "> **DRAFT FOR HUMAN REVIEW — NOT INVESTMENT ADVICE.** Prepared by DealLens Diligence Lab "
            f"from {t.name}'s SEC filings. Financial figures are from XBRL company facts; qualitative "
            "flags are from the 10-K. Bracketed tags like `[EV-001]` link to the evidence table."
        )
        lines.append("")

        # Executive summary
        lines.append("## 1. Executive Summary")
        lines.append(
            f"{t.name} reported revenue of {_usd(t.revenue)}{_cite(r.get('revenue'))} in FY"
            f"{(t.fiscal_year_end or '')[:4]}, growing {_pct(t.revenue_growth)}{_cite(r.get('revenue_growth'))} "
            f"at a {_pct(t.gross_margin)} gross margin{_cite(r.get('gross_margin'))} and "
            f"{_pct(t.operating_margin)} GAAP operating margin{_cite(r.get('operating_margin'))} "
            f"(Rule of 40 ~ {_pct(t.rule_of_40)}{_cite(r.get('rule_of_40'))}). "
            f"Diligence surfaced {len(findings)} red flag(s), {len(high)} high-severity. "
            "Recommendation: advance to confirmatory diligence on the highest-severity items below."
        )
        lines.append("")

        lines.append("## 2. Investment Question")
        lines.append(iq or f"Assess {t.name} as an investment.")
        lines.append("")

        lines.append("## 3. Company Overview")
        lines.append(f"*Sector: {t.sector or 'n/a'}.* {t.description or ''}")
        lines.append("")

        # Financial profile
        lines.append("## 4. Financial Profile (SEC XBRL)")
        lines.append("| Metric | Value | Evidence |")
        lines.append("|---|---|---|")
        rows = [
            ("Revenue", _usd(t.revenue), r.get("revenue")),
            ("Revenue growth", _pct(t.revenue_growth), r.get("revenue_growth")),
            ("Gross margin", _pct(t.gross_margin), r.get("gross_margin")),
            ("Operating margin", _pct(t.operating_margin), r.get("operating_margin")),
            ("Net income", _usd(t.net_income), r.get("net_income")),
            ("Net margin", _pct(t.net_margin), r.get("net_margin")),
            ("R&D % of revenue", _pct(t.rnd_pct), r.get("rnd_pct")),
            ("Rule of 40", _pct(t.rule_of_40), r.get("rule_of_40")),
            ("Cash", _usd(t.cash), r.get("cash")),
            ("Total debt", _usd(t.total_debt), r.get("total_debt")),
        ]
        trends = ctx.get("trends") or {}
        if trends.get("revenue_cagr") is not None and trends.get("years"):
            yrs = trends["years"]
            rows.append(
                (f"Revenue CAGR ({len(yrs)}-yr)", _pct(trends["revenue_cagr"]), r.get("revenue_cagr"))
            )
        for label, val, ref in rows:
            lines.append(f"| {label} | {val} | {('`'+ref+'`') if ref else '—'} |")
        lines.append("")

        # GovCon profile (Release 0.5) — only when federal award data is present.
        gc = ctx.get("govcon")
        if gc and getattr(gc, "total_obligations", 0):
            lines.append("## Federal Contract Profile (GovCon)")
            lines.append(
                f"{_usd(gc.total_obligations)} in federal contract obligations across "
                f"{gc.award_count} contract action(s) (USAspending.gov)."
            )
            if gc.top_agency and gc.top_agency_pct is not None:
                lines.append(
                    f"Top agency: **{gc.top_agency}** at {gc.top_agency_pct*100:.0f}% of obligations "
                    f"(single-agency concentration to diligence)."
                )
            rc = gc.recompete or {}
            if rc.get("count"):
                lines.append(
                    f"Recompete exposure: {rc['count']} major award(s) (~{_usd(rc.get('value'))}) "
                    f"have periods of performance ending within 24 months."
                )
            lines.append("")

        # Comps / benchmark
        lines.append("## 5. Public Comps & Benchmark")
        if bench and bench.get("peer_count"):
            lines.append(bench["summary"])
            lines.append("")
            lines.append("| Metric | Target | Peer median | Read |")
            lines.append("|---|---|---|---|")
            for m in bench["metrics"]:
                tv = _fmt_metric(m["target_value"], m["unit"])
                pm = _fmt_metric(m["peer_median"], m["unit"])
                lines.append(f"| {m['label']} | {tv} | {pm} | {m['assessment']} |")
        else:
            lines.append(
                "No public peer set has been added yet. Add comparable tickers on the Comps tab to "
                "populate a real XBRL-based benchmark."
            )
        lines.append("")

        # Key findings / red flags
        lines.append("## 6. Key Diligence Findings & Red Flags")
        if top:
            for f in top:
                lines.append(
                    f"- **{f['title']}** ({f['severity']}, {f['severity_score']}/10) — "
                    f"{f['finding']}{_cite(f.get('evidence_ref'))}"
                )
        else:
            lines.append("- No material red flags were surfaced from the filing or financials.")
        lines.append("")

        lines.append("## 7. Open Questions")
        for f in top[:5]:
            lines.append(f"- {f['follow_up_question']}{_cite(f.get('evidence_ref'))}")
        lines.append("")

        lines.append("## 8. Preliminary Thesis")
        lines.append(
            f"{t.name} is a {_shape(t)} with the profile above. The thesis hinges on resolving the "
            f"{len(high)} high-severity item(s); pending that, this is a candidate to advance, not to price."
        )
        lines.append("")

        lines.append("## 9. Recommended Next Diligence Steps")
        lines.append("1. Financial: independent quality-of-earnings and unit-economics validation.")
        lines.append("2. Commercial: bookings-by-driver and win/loss analysis.")
        lines.append("3. Legal/technology: confirm the disclosed risk factors quantitatively.")
        lines.append("")
        lines.append("## Appendix: Evidence")
        lines.append(
            "Every `[EV-###]` tag resolves to a row in the Evidence & Audit table with the claim type, "
            "SEC source, snippet, and confidence. **Draft for human review — not investment advice.**"
        )
        return "\n".join(lines)


def _fmt_metric(v, unit):
    if v is None:
        return "—"
    if unit == "pct":
        return f"{v*100:.1f}%"
    if unit == "usd":
        return _usd(v)
    if unit == "x":
        return f"{v:.1f}x"
    return f"{v:.2f}"


def _shape(t) -> str:
    if t.operating_margin is not None and t.operating_margin < 0:
        return "growth-stage, not-yet-profitable public company"
    if t.revenue_growth is not None and t.revenue_growth > 0.2:
        return "high-growth, profitable public company"
    return "public company"
