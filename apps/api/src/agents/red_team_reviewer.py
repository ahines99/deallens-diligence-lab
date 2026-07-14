"""Red-team reviewer — challenges the thesis and drafts the bear case from real findings."""
from __future__ import annotations

from src.agents.base import BaseAgent
from src.agents.ic_memo_writer import _cite, _pct


class RedTeamReviewer(BaseAgent):
    name = "red_team_reviewer"
    role = "Argues the skeptical side: what's weakly supported, what's missing, what breaks the thesis."

    def build(self, ctx: dict) -> dict:
        t = ctx["target"]
        findings = ctx["findings"]
        r = ctx["fin_refs"]
        top = sorted(findings, key=lambda f: f["severity_score"], reverse=True)[:5]
        text_findings = [f for f in findings if f["evidence"]["claim_type"] == "fact"] if findings else []

        # Bear-case markdown
        b: list[str] = []
        b.append(f"# Bear-Case / Red-Team Memo — {t.name} ({t.ticker or 'private'}) (DRAFT)")
        b.append("")
        b.append(
            "> **DRAFT FOR HUMAN REVIEW — NOT INVESTMENT ADVICE.** This memo argues the skeptical side to "
            "stress-test the thesis, using the company's own SEC disclosures."
        )
        b.append("")
        b.append("## Thesis Under Attack")
        b.append(
            f"The base case leans on {t.name}'s reported metrics. The bear case is that the disclosed "
            "risks and financial profile are more binding than the headline suggests."
        )
        b.append("")
        if t.operating_margin is not None and t.operating_margin < 0:
            b.append(
                f"**Profitability.** {t.name} runs a negative GAAP operating margin of "
                f"{_pct(t.operating_margin)}{_cite(r.get('operating_margin'))}; the path to profitability is "
                "an assumption, not a fact, and is sensitive to growth and spend discipline."
            )
            b.append("")
        if t.revenue_growth is not None and t.revenue_growth < 0.1:
            b.append(
                f"**Growth.** Revenue growth of {_pct(t.revenue_growth)}{_cite(r.get('revenue_growth'))} leaves "
                "little margin for error if demand softens further."
            )
            b.append("")
        b.append("## Disclosed Risks the Base Case May Underweight")
        for f in top:
            b.append(f"- **{f['risk_category_label']}.** {f['finding']}{_cite(f.get('evidence_ref'))}")
        b.append("")
        b.append("## What Would Break the Thesis")
        b.append("- A high-severity risk factor proving quantitatively material once diligenced.")
        b.append("- Growth or margin deterioration beyond the current trajectory.")
        b.append("- Quality-of-earnings adjustments that reduce the sustainable profit base.")
        b.append("")
        b.append(
            "**Bottom line:** advance only after the high-severity items are quantified. The burden of proof "
            "is on the thesis, not the disclosures. Draft for human review; not investment advice."
        )
        bear_md = "\n".join(b)

        # Unsupported claims — text-based findings rest on disclosure language, not quantified exposure.
        unsupported = []
        for f in text_findings[:4]:
            unsupported.append(
                {
                    "claim": f"The {f['risk_category_label'].lower()} risk is manageable at the current severity.",
                    "why_weak": (
                        "The finding is drawn from 10-K risk-factor language, which states the risk but does "
                        "not quantify exposure; severity here is a heuristic, not a measured value."
                    ),
                    "recommended_action": f"Quantify the {f['risk_category_label'].lower()} exposure with primary data.",
                }
            )
        if t.operating_margin is not None and t.operating_margin < 0:
            unsupported.append(
                {
                    "claim": "The company will reach profitability on plan.",
                    "why_weak": f"Current GAAP operating margin is {_pct(t.operating_margin)}; profitability is projected, not demonstrated.",
                    "recommended_action": "Stress-test the path-to-profitability model under a downside case.",
                }
            )
        if not unsupported:
            unsupported.append(
                {
                    "claim": "Headline metrics fully capture the risk profile.",
                    "why_weak": "Financial metrics are point-in-time; forward risks require primary diligence.",
                    "recommended_action": "Validate forward drivers with management and third-party data.",
                }
            )

        # Missing evidence — standard gaps a filing can't answer.
        missing = [
            {"item": "Quality-of-earnings analysis", "why_it_matters": "Confirms the sustainable profit/EBITDA base behind the reported margins.", "workstream": "financial"},
            {"item": "Cohort retention / unit economics", "why_it_matters": "Filings rarely disclose cohort NRR/GRR or CAC payback; these drive the growth-quality thesis.", "workstream": "customer"},
            {"item": "Quantified exposure for each high-severity risk factor", "why_it_matters": "Risk factors state risks qualitatively; magnitude must be sized in diligence.", "workstream": "commercial"},
        ]
        # High-priority questions — from top findings + a QoE anchor.
        hpq = []
        for f in top[:4]:
            hpq.append(
                {
                    "workstream": f["workstream_owner"],
                    "workstream_label": f["risk_category_label"],
                    "question": f["follow_up_question"],
                    "rationale": f"Addresses the '{f['title']}' finding.",
                    "priority": "high",
                }
            )
        hpq.append(
            {
                "workstream": "financial",
                "workstream_label": "Financial diligence",
                "question": "Can an independent QoE reconcile reported margins to sustainable cash earnings?",
                "rationale": "The earnings base underpins any leverage and valuation view.",
                "priority": "high",
            }
        )

        summary = (
            f"The thesis for {t.name} is credible but rests on resolving {len(top)} disclosed risk(s) and "
            "confirming the sustainability of the reported financial profile. Several base-case claims are "
            "disclosure-based rather than quantified and should not be relied on until diligenced."
        )
        return {
            "summary": summary,
            "bear_case_markdown": bear_md,
            "unsupported_claims": unsupported,
            "missing_evidence": missing,
            "high_priority_questions": hpq,
        }
