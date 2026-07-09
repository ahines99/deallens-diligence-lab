"""Diligence lead — builds the diligence plan and workstream question list from real findings."""
from __future__ import annotations

from src.agents.base import BaseAgent
from src.seed import loader

_OBJECTIVES = {
    "commercial": "Test demand durability, competitive position, and growth quality.",
    "product_technology": "Assess architecture, technical moat, scalability, and key dependencies.",
    "financial": "Confirm quality of earnings, unit economics, and the value-creation bridge.",
    "customer": "Validate retention, concentration, and mission-criticality of the product.",
    "market": "Bound TAM/SAM, growth, and macro/cyclical sensitivity.",
    "legal_regulatory": "Clear litigation, IP, data-rights, and regulatory exposure.",
    "cybersecurity": "Validate security posture for sensitive data and disclosed incidents.",
    "ai_data": "Assess data rights, AI exposure, and model/vendor dependencies.",
    "management": "Assess team depth, key-person risk, and post-close retention.",
    "govcon": "Assess federal contract concentration and recompete risk (if applicable).",
}
_EVIDENCE_NEEDED = {
    "commercial": ["Bookings by quarter and driver", "Win/loss analysis", "Pipeline coverage"],
    "product_technology": ["Architecture review", "Tech-debt assessment", "Key-dependency map"],
    "financial": ["Quality-of-earnings report", "Cohort unit economics", "Cost-of-revenue detail"],
    "customer": ["Cohort retention data", "Top-customer contracts", "Usage telemetry"],
    "market": ["TAM/SAM sizing", "Competitive map", "Macro sensitivity analysis"],
    "legal_regulatory": ["Litigation schedule", "IP/open-source inventory", "Data-rights review"],
    "cybersecurity": ["SOC 2 / pen-test reports", "Data-handling controls"],
    "ai_data": ["Data-license terms", "Model governance", "AI-competition assessment"],
    "management": ["Org chart", "Management references", "Retention/equity plan"],
    "govcon": ["Contract/recompete schedule", "Agency concentration"],
}


class DiligenceLead(BaseAgent):
    name = "diligence_lead"
    role = "Owns the diligence plan and coordinates diligence questions by workstream."

    def build_plan(self, target, investment_question: str, findings: list[dict]) -> dict:
        templates = {t["slug"]: t for t in loader.question_templates()}
        cats_with_findings = {f["risk_category"] for f in findings}
        owner_ws = {f["workstream_owner"] for f in findings}
        workstreams = []
        for slug, tmpl in templates.items():
            if slug == "govcon" and "govcon" not in owner_ws and "govcon_risk" not in cats_with_findings:
                status = "complete"
                objective = "Not applicable — no material federal-contract exposure identified."
                key_qs: list[str] = []
                evneeded: list[str] = []
            else:
                status = "planned"
                objective = _OBJECTIVES.get(slug, "")
                key_qs = [q.replace("{target}", target.name) for q in tmpl.get("questions", [])[:2]]
                evneeded = _EVIDENCE_NEEDED.get(slug, [])
            workstreams.append(
                {
                    "workstream": slug,
                    "workstream_label": tmpl["label"],
                    "objective": objective,
                    "key_questions": key_qs,
                    "evidence_needed": evneeded,
                    "status": status,
                }
            )
        summary = (
            f"First-pass diligence plan for {target.name} ({target.ticker or 'private'}). "
            f"{len(findings)} red flag(s) identified from the latest 10-K and XBRL financials drive the "
            f"priority workstreams; confirm the highest-severity items before deeper diligence."
        )
        return {"summary": summary, "workstreams": workstreams}

    def build_questions(self, target, findings: list[dict]) -> list[dict]:
        templates = {t["slug"]: t for t in loader.question_templates()}
        questions: list[dict] = []
        seen: set[str] = set()

        # 1) Finding-driven questions (high priority, evidence-linked).
        for f in findings:
            q = f.get("follow_up_question", "").strip()
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
            ws = f["workstream_owner"]
            questions.append(
                {
                    "workstream": ws,
                    "workstream_label": templates.get(ws, {}).get("label", ws.replace("_", " ").title()),
                    "question": q,
                    "rationale": f"Follows from the '{f['title']}' finding.",
                    "priority": "high" if f["severity"] in ("high", "critical") else "medium",
                    "evidence_ref": f.get("evidence_ref"),
                }
            )

        # 2) Standard workstream questions (fill coverage).
        for slug, tmpl in templates.items():
            for q in tmpl.get("questions", [])[:2]:
                qtext = q.replace("{target}", target.name)
                if qtext.lower() in seen:
                    continue
                seen.add(qtext.lower())
                questions.append(
                    {
                        "workstream": slug,
                        "workstream_label": tmpl["label"],
                        "question": qtext,
                        "rationale": "Standard workstream coverage.",
                        "priority": "medium",
                        "evidence_ref": None,
                    }
                )
        return questions
