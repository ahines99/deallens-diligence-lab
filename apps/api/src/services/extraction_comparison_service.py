"""G79 — persisted extraction comparison: G52's extractor-vs-scanner diff as a governed record.

``run_and_persist`` runs :func:`llm_risk_extractor.compare_with_scanner` over a workspace's
filing chunks — the same chunk pool, taxonomy, and filing context the full analysis feeds the
extractor — and seals the resulting category diff as an append-only ``ArtifactVersion``
(``artifact_type="extraction_comparison"``), so the overlap/llm-only/scanner-only story
accumulates over time instead of evaporating with the response.

Consent and honesty match every other LLM path: the comparison requires workspace consent
(``external_llm_allowed`` and a non-``restricted`` classification), live mode, and an API key.
Whenever the structured-LLM substrate does not apply (mock CI, no consent, no key, provider or
parse failure) — or the workspace has no chunks to compare — the run returns an honest
``{"status": "not_run", "reason": ...}`` and persists NOTHING: an absent comparison must never
read as "the engines agree". A live run whose proposals all failed span verification
(``no_verified_findings``) IS persisted — the LLM genuinely verified zero categories, and that
is real comparison data, not a substrate outage.

``latest`` serves the newest persisted comparison across workspaces for the ``/quality``
dashboard's ``extraction_comparison`` section, with the same explicit-status discipline as the
other sections.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents import llm_risk_extractor
from src.models import DocumentChunk, Filing, Target
from src.models.underwriting_data import ArtifactVersion
from src.seed import loader
from src.services.common import NotFound, get_workspace_or_404, insert_versioned

_ARTIFACT_TYPE = "extraction_comparison"

# Substrate-applied reason values: a live LLM answered and its output was verifiable, so the
# comparison is real data worth persisting. Every other reason (no_consent / mock / no_api_key /
# error / parse_error / schema_mismatch) means no LLM output exists to compare against.
_APPLIED_REASONS = {"applied", "no_verified_findings"}


def _not_run(workspace_id: str, reason: str) -> dict:
    """Honest non-result: nothing was compared, nothing is persisted, and the caller knows why."""
    return {"status": "not_run", "workspace_id": workspace_id, "reason": reason}


def run_and_persist(session: Session, workspace_id: str, *, provider_factory=None) -> dict:
    """Run the G52 comparison for one workspace and seal it as an append-only artifact.

    Returns the persisted comparison (``status="available"``) or a ``not_run`` provenance dict
    when the LLM substrate does not apply. ``provider_factory`` exists for tests, exactly as at
    the ``structured_llm`` seam.
    """
    ws = get_workspace_or_404(session, workspace_id)
    target = session.scalar(select(Target).where(Target.workspace_id == workspace_id))
    if target is None:
        raise NotFound("No target ingested for this workspace. Create it with a ticker first.")

    # Same consent + classification semantics as analysis_service (G51 gating).
    external_allowed = ws.external_llm_allowed and ws.data_classification != "restricted"

    # Same filing context the analysis pass builds, so both engines cite the same source.
    tenk = session.scalar(
        select(Filing)
        .where(Filing.workspace_id == workspace_id, Filing.form_type == "10-K")
        .order_by(Filing.filing_date.desc())
    )
    filing_ctx = {
        "company": target.name,
        "url": tenk.document_url if tenk else None,
        "date": tenk.filing_date if tenk else target.fiscal_year_end,
    }

    chunks = list(
        session.scalars(select(DocumentChunk).where(DocumentChunk.workspace_id == workspace_id))
    )
    if not chunks:
        # Nothing for either engine to read — refuse before any provider could be constructed.
        return _not_run(workspace_id, "no_document_chunks")

    comparison = llm_risk_extractor.compare_with_scanner(
        chunks,
        loader.risk_taxonomy(),
        filing_ctx,
        external_allowed=external_allowed,
        provider_factory=provider_factory,
    )
    provenance = comparison["llm_provenance"]
    if provenance["reason"] not in _APPLIED_REASONS:
        return _not_run(workspace_id, provenance["reason"])

    record = {
        "workspace_id": workspace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "both": comparison["both"],
        "llm_only": comparison["llm_only"],
        "scanner_only": comparison["scanner_only"],
        "llm_provenance": provenance,
        "manifest": provenance["manifest"],
    }

    from src.services import underwriting_data_service

    def _build_artifact() -> ArtifactVersion:
        latest_row = session.scalar(
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
            version=(latest_row.version + 1) if latest_row else 1,
            supersedes_id=latest_row.id if latest_row else None,
            analysis_run_id=None,
            source_snapshot_ids=[],
            input_hash=underwriting_data_service.content_hash(
                {"workspace_id": workspace_id, "manifest": record["manifest"]}
            ),
            content_hash=underwriting_data_service.content_hash(record),
            content_json=record,
            content_text=None,
            file_uri=None,
            artifact_metadata={
                "engine": provenance["engine"],
                "verified": provenance["verified"],
                "overlap": len(record["both"]),
            },
            created_by="extraction_comparison",
        )

    artifact = insert_versioned(session, _build_artifact)
    session.commit()
    return {
        "status": "available",
        "artifact_version_id": artifact.id,
        "version": artifact.version,
        **record,
    }


def latest(session: Session) -> dict:
    """The newest persisted comparison across workspaces, shaped for the quality dashboard.

    ``{"status": "available", "note": None, "workspace_id", "generated_at", "both", "llm_only",
    "scanner_only"}`` — or an explicit ``unavailable`` with a note when no comparison has ever
    been run, never a fabricated empty diff.
    """
    row = session.scalar(
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_type == _ARTIFACT_TYPE)
        .order_by(ArtifactVersion.created_at.desc(), ArtifactVersion.version.desc())
    )
    if row is None:
        return {"status": "unavailable", "note": "no extraction comparison has been run yet"}
    content = row.content_json or {}
    return {
        "status": "available",
        "note": None,
        "workspace_id": content.get("workspace_id", row.workspace_id),
        "generated_at": content.get("generated_at"),
        "both": content.get("both", []),
        "llm_only": content.get("llm_only", []),
        "scanner_only": content.get("scanner_only", []),
    }
