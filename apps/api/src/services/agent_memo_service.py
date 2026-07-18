"""G59 — agent-drafted IC memo sections with per-section grounding and human accept/reject.

The agent drafts the memo one section at a time by running the full G57 loop
(:func:`agent_service.run_diligence_agent`) once per section, so every section inherits the
governed tool allowlist, the per-run sealed transcript, and — critically — an INDEPENDENT
fail-closed grounding gate: a section whose prose contains any quantity token or ``EV-###``
reference that its OWN tool results (plus its section brief) never produced is withheld, while
the other sections survive. One fabricated number cannot poison the whole memo, and a withheld
section serves no text at all.

The draft state itself is a governed record: an append-only ``ArtifactVersion``
(``artifact_type="agent_memo_draft"``) whose ``content_json`` carries the section entries and
their review decisions. Human decisions NEVER mutate a draft — each accept/reject mints a NEW
version superseding the previous one, so the review trail is tamper-evident by construction.
Only sections a HUMAN accepted enter ``assembled_markdown`` (built when every drafted section
has been decided); the agent proposes, the analyst disposes — the four-eyes boundary holds.

Consent gating matches ``run_diligence_agent`` exactly: workspace ``external_llm_allowed`` and a
non-``restricted`` classification, live mode, and an API key — otherwise ``status="not_run"``
with a machine-readable reason, zero provider constructions, and NOTHING persisted (an absent
draft must never read as "the agent had nothing to say").
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import settings
from src.models.underwriting_data import ArtifactVersion
from src.services import agent_service
from src.services.common import NotFound, get_workspace_or_404, insert_versioned

_ARTIFACT_TYPE = "agent_memo_draft"

# The fixed IC memo section plan. Deliberately a constant: the agent chooses prose, never scope.
SECTION_PLAN: tuple[str, ...] = (
    "Business overview",
    "Financial performance",
    "Key risks",
    "Underwriting view",
)

# Section briefs feed the per-section objective. They are deliberately number-free so the brief
# can never launder a quantity token past the section's own grounding gate.
_SECTION_BRIEFS: dict[str, str] = {
    "Business overview": (
        "what the company does, its market position, and its operating model, grounded in the "
        "ingested filings and the workspace overview"
    ),
    "Financial performance": (
        "the revenue, margin, and cash trajectory using only the figures the workspace tools "
        "report"
    ),
    "Key risks": (
        "the material risks with their severities and EV-### evidence references from the risk "
        "register"
    ),
    "Underwriting view": (
        "the saved underwriting cases and the headline returns the deterministic engine reports"
    ),
}

_RUN_STATUS_TO_SECTION_STATUS = {
    "completed": "drafted",
    "rejected_ungrounded": "withheld",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _section_objective(section: str) -> str:
    return (
        f"Draft the '{section}' section of an investment-committee memo, covering "
        f"{_SECTION_BRIEFS[section]}. Use only facts returned by your tools, cite EV-### "
        "references where the tools provide them, and state explicitly when the tools do not "
        "support a claim."
    )


def _not_run(workspace_id: str, reason: str) -> dict:
    """Honest non-result: no provider was constructed and nothing was persisted."""
    return {
        "workspace_id": workspace_id,
        "status": "not_run",
        "reason": reason,
        "sections": [],
        "generated_at": _now_iso(),
        "draft_artifact_id": None,
        "version": None,
        "assembled_markdown": None,
    }


def _serialize(artifact: ArtifactVersion) -> dict:
    content = artifact.content_json or {}
    return {
        "workspace_id": content.get("workspace_id", artifact.workspace_id),
        "status": content.get("status", "in_review"),
        "reason": None,
        "sections": content.get("sections", []),
        "generated_at": content.get("generated_at"),
        "draft_artifact_id": artifact.id,
        "version": artifact.version,
        "assembled_markdown": content.get("assembled_markdown"),
    }


def _latest_row(session: Session, workspace_id: str) -> ArtifactVersion | None:
    return session.scalar(
        select(ArtifactVersion)
        .where(
            ArtifactVersion.workspace_id == workspace_id,
            ArtifactVersion.artifact_type == _ARTIFACT_TYPE,
        )
        .order_by(ArtifactVersion.version.desc())
    )


def _persist_draft(
    session: Session,
    workspace_id: str,
    content: dict,
    *,
    created_by: str,
    input_seed: dict,
) -> ArtifactVersion:
    from src.services import underwriting_data_service

    def _build() -> ArtifactVersion:
        latest = _latest_row(session, workspace_id)
        return ArtifactVersion(
            workspace_id=workspace_id,
            artifact_type=_ARTIFACT_TYPE,
            version=(latest.version + 1) if latest else 1,
            supersedes_id=latest.id if latest else None,
            analysis_run_id=None,
            source_snapshot_ids=[],
            input_hash=underwriting_data_service.content_hash(input_seed),
            content_hash=underwriting_data_service.content_hash(content),
            content_json=content,
            content_text=None,
            file_uri=None,
            artifact_metadata={
                "status": content["status"],
                "drafted": sum(1 for s in content["sections"] if s["status"] == "drafted"),
                "withheld": sum(1 for s in content["sections"] if s["status"] == "withheld"),
            },
            created_by=created_by,
        )

    return insert_versioned(session, _build)


def draft_sections(
    session: Session,
    workspace_id: str,
    *,
    actor_id: str | None = None,
    provider_factory=None,
    max_steps_per_section: int = 6,
) -> dict:
    """Draft every planned section through its own G57 run; seal the draft state; return it.

    ``provider_factory`` is invoked once PER SECTION (the G57 loop constructs one provider per
    run), so tests can script each section independently. A section whose run fails the
    grounding gate is recorded as ``withheld`` with its violations and NO text; the remaining
    sections keep their drafts — per-section isolation is the point of the feature.
    """
    ws = get_workspace_or_404(session, workspace_id)

    # Same gating semantics as run_diligence_agent, checked BEFORE any per-section run so a
    # gated draft persists nothing and constructs no provider.
    external_allowed = ws.external_llm_allowed and ws.data_classification != "restricted"
    if not external_allowed:
        return _not_run(workspace_id, "no_consent")
    if settings.is_mock:
        return _not_run(workspace_id, "mock")
    if not settings.llm_api_key:
        return _not_run(workspace_id, "no_api_key")

    sections: list[dict] = []
    for section in SECTION_PLAN:
        run = agent_service.run_diligence_agent(
            session,
            workspace_id,
            _section_objective(section),
            actor_id=actor_id,
            max_steps=max_steps_per_section,
            provider_factory=provider_factory,
        )
        sections.append(
            {
                "section": section,
                "status": _RUN_STATUS_TO_SECTION_STATUS.get(run["status"], "error"),
                "answer": run["answer"] if run["status"] == "completed" else None,
                "grounding": run["grounding"],
                "artifact_version_id": run["artifact_version_id"],
                "decision": "pending",
                "decided_by": None,
                "decided_at": None,
            }
        )

    content = {
        "workspace_id": workspace_id,
        "generated_at": _now_iso(),
        "status": "in_review",
        "sections": sections,
    }
    artifact = _persist_draft(
        session,
        workspace_id,
        content,
        created_by=actor_id or "agent_memo",
        input_seed={
            "workspace_id": workspace_id,
            "section_plan": list(SECTION_PLAN),
            "generated_at": content["generated_at"],
        },
    )
    session.commit()
    return _serialize(artifact)


def decide_section(
    session: Session,
    workspace_id: str,
    draft_artifact_id: str,
    section: str,
    decision: str,
    *,
    actor_id: str | None,
) -> dict:
    """Record a HUMAN accept/reject by minting a NEW superseding draft version (append-only).

    The base draft is never mutated. When every ``drafted`` section carries a decision, the new
    version's ``assembled_markdown`` is built from the ACCEPTED sections only and the draft
    status becomes ``decided`` — withheld and rejected text never reaches the assembled memo.
    """
    if not actor_id:
        raise ValueError(
            "A section decision requires an authenticated actor "
            "(session principal or X-Actor-ID header)."
        )
    if decision not in ("accept", "reject"):
        raise ValueError("decision must be 'accept' or 'reject'")
    get_workspace_or_404(session, workspace_id)

    row = session.get(ArtifactVersion, draft_artifact_id)
    if row is None or row.workspace_id != workspace_id or row.artifact_type != _ARTIFACT_TYPE:
        raise NotFound(f"Agent memo draft '{draft_artifact_id}' not found")
    latest = _latest_row(session, workspace_id)
    if latest is None or row.id != latest.id:
        raise ValueError(
            "This draft version has been superseded; fetch the latest draft and decide there."
        )

    content = copy.deepcopy(latest.content_json or {})
    sections = content.get("sections") or []
    entry = next((s for s in sections if s.get("section") == section), None)
    if entry is None:
        raise ValueError(f"Unknown section: {section!r}")
    if entry.get("status") != "drafted":
        raise ValueError(
            f"Section {section!r} is {entry.get('status')!r}; only drafted sections can be "
            "accepted or rejected."
        )

    entry["decision"] = decision
    entry["decided_by"] = actor_id
    entry["decided_at"] = _now_iso()

    drafted = [s for s in sections if s.get("status") == "drafted"]
    if drafted and all(s.get("decision") in ("accept", "reject") for s in drafted):
        content["status"] = "decided"
        content["assembled_markdown"] = "\n\n".join(
            f"## {s['section']}\n\n{s.get('answer') or ''}"
            for s in drafted
            if s["decision"] == "accept"
        )
    else:
        content["status"] = "in_review"
        content.pop("assembled_markdown", None)

    artifact = _persist_draft(
        session,
        workspace_id,
        content,
        created_by=actor_id,
        input_seed={
            "workspace_id": workspace_id,
            "base_draft_id": latest.id,
            "section": section,
            "decision": decision,
        },
    )
    session.commit()
    return _serialize(artifact)


def get_latest_draft(session: Session, workspace_id: str) -> dict | None:
    """The newest ``agent_memo_draft`` for the workspace, or ``None`` when none exists."""
    get_workspace_or_404(session, workspace_id)
    row = _latest_row(session, workspace_id)
    return _serialize(row) if row is not None else None
