"""Read-only timeline across workflow, data, intelligence, and underwriting planes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_intelligence import ClaimReviewEvent, DataRoomDocument
from src.models.deal_workflow import Deal, Organization, WorkflowAuditEvent
from src.models.underwriting_data import AnalysisRun, SourceSnapshot
from src.models.underwriting_model import UnderwritingCaseDecision, UnderwritingCaseVersion
from src.models.workspace import Workspace


class ActivityNotFound(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_timeline(
    session: Session,
    organization_id: str,
    *,
    deal_id: str | None = None,
    actor_id: str | None = None,
    category: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if session.get(Organization, organization_id) is None:
        raise ActivityNotFound("Organization not found")
    deals = list(
        session.scalars(select(Deal).where(Deal.organization_id == organization_id))
    )
    if deal_id and deal_id not in {item.id for item in deals}:
        raise ActivityNotFound("Deal not found")
    if deal_id:
        deals = [item for item in deals if item.id == deal_id]
    deal_ids = [item.id for item in deals]
    workspace_to_deal = {
        item.workspace_id: item.id for item in deals if item.workspace_id is not None
    }
    workspace_ids = list(workspace_to_deal)
    items: list[dict[str, Any]] = []

    workflow_statement = select(WorkflowAuditEvent).where(
        WorkflowAuditEvent.organization_id == organization_id
    )
    if deal_id:
        workflow_statement = workflow_statement.where(WorkflowAuditEvent.deal_id == deal_id)
    for event in session.scalars(workflow_statement):
        items.append(
            {
                "id": f"workflow:{event.id}",
                "source": "workflow_audit",
                "category": "workflow",
                "event_type": event.action,
                "summary": event.action.replace(".", " ").replace("_", " ").title(),
                "organization_id": organization_id,
                "deal_id": event.deal_id,
                "workspace_id": next(
                    (key for key, value in workspace_to_deal.items() if value == event.deal_id),
                    None,
                ),
                "actor_id": event.actor_id,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "detail": event.detail or {},
                "occurred_at": event.created_at,
            }
        )

    if deal_ids:
        for document in session.scalars(
            select(DataRoomDocument).where(DataRoomDocument.deal_id.in_(deal_ids))
        ):
            items.append(
                {
                    "id": f"document:{document.id}",
                    "source": "data_room",
                    "category": "intelligence",
                    "event_type": "document.version.created",
                    "summary": f"Data-room document v{document.version}: {document.title}",
                    "organization_id": organization_id,
                    "deal_id": document.deal_id,
                    "workspace_id": None,
                    "actor_id": document.uploaded_by_actor_id,
                    "entity_type": "DataRoomDocument",
                    "entity_id": document.id,
                    "detail": {
                        "version": document.version,
                        "filename": document.filename,
                        "sha256": document.sha256,
                    },
                    "occurred_at": document.created_at,
                }
            )
        for review in session.scalars(
            select(ClaimReviewEvent).where(ClaimReviewEvent.deal_id.in_(deal_ids))
        ):
            items.append(
                {
                    "id": f"claim-review:{review.id}",
                    "source": "intelligence_review",
                    "category": "intelligence",
                    "event_type": f"claim.{review.action}",
                    "summary": f"Claim revision {review.to_revision} {review.action}d",
                    "organization_id": organization_id,
                    "deal_id": review.deal_id,
                    "workspace_id": None,
                    "actor_id": review.reviewer_actor_id,
                    "entity_type": "StructuredClaim",
                    "entity_id": review.to_claim_id,
                    "detail": {
                        "logical_claim_id": review.logical_claim_id,
                        "from_revision": review.from_revision,
                        "to_revision": review.to_revision,
                        "resulting_status": review.resulting_status,
                    },
                    "occurred_at": review.created_at,
                }
            )

    if workspace_ids:
        for snapshot in session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.workspace_id.in_(workspace_ids))
        ):
            items.append(
                {
                    "id": f"source:{snapshot.id}",
                    "source": "source_snapshot",
                    "category": "data",
                    "event_type": "source.sealed",
                    "summary": f"{snapshot.source_name} v{snapshot.version} sealed ({snapshot.status})",
                    "organization_id": organization_id,
                    "deal_id": workspace_to_deal.get(snapshot.workspace_id),
                    "workspace_id": snapshot.workspace_id,
                    "actor_id": snapshot.created_by,
                    "entity_type": "SourceSnapshot",
                    "entity_id": snapshot.id,
                    "detail": {
                        "source_type": snapshot.source_type,
                        "status": snapshot.status,
                        "record_count": snapshot.record_count,
                        "content_hash": snapshot.content_hash,
                    },
                    "occurred_at": snapshot.created_at,
                }
            )
        for run in session.scalars(
            select(AnalysisRun).where(AnalysisRun.workspace_id.in_(workspace_ids))
        ):
            items.append(
                {
                    "id": f"analysis:{run.id}",
                    "source": "analysis_run",
                    "category": "analysis",
                    "event_type": f"analysis.{run.status}",
                    "summary": f"{run.run_type} v{run.version} {run.status}",
                    "organization_id": organization_id,
                    "deal_id": workspace_to_deal.get(run.workspace_id),
                    "workspace_id": run.workspace_id,
                    "actor_id": run.created_by,
                    "entity_type": "AnalysisRun",
                    "entity_id": run.id,
                    "detail": {
                        "run_type": run.run_type,
                        "version": run.version,
                        "input_hash": run.input_hash,
                        "content_hash": run.content_hash,
                    },
                    "occurred_at": run.completed_at,
                }
            )
        versions = list(
            session.scalars(
                select(UnderwritingCaseVersion).where(
                    UnderwritingCaseVersion.workspace_id.in_(workspace_ids)
                )
            )
        )
        version_by_id = {item.id: item for item in versions}
        for version in versions:
            items.append(
                {
                    "id": f"case:{version.id}",
                    "source": "underwriting_model",
                    "category": "underwriting",
                    "event_type": "case.version.created",
                    "summary": f"{version.case_key.title()} case v{version.version} created",
                    "organization_id": organization_id,
                    "deal_id": workspace_to_deal.get(version.workspace_id),
                    "workspace_id": version.workspace_id,
                    "actor_id": version.created_by,
                    "entity_type": "UnderwritingCaseVersion",
                    "entity_id": version.id,
                    "detail": {
                        "case_key": version.case_key,
                        "version": version.version,
                        "input_hash": version.input_hash,
                        "output_hash": version.output_hash,
                    },
                    "occurred_at": version.created_at,
                }
            )
        if version_by_id:
            for decision in session.scalars(
                select(UnderwritingCaseDecision).where(
                    UnderwritingCaseDecision.case_version_id.in_(list(version_by_id))
                )
            ):
                version = version_by_id[decision.case_version_id]
                items.append(
                    {
                        "id": f"case-decision:{decision.id}",
                        "source": "underwriting_governance",
                        "category": "governance",
                        "event_type": f"case.{decision.decision}",
                        "summary": f"{version.case_key.title()} case {decision.decision}",
                        "organization_id": organization_id,
                        "deal_id": workspace_to_deal.get(decision.workspace_id),
                        "workspace_id": decision.workspace_id,
                        "actor_id": decision.actor,
                        "entity_type": "UnderwritingCaseDecision",
                        "entity_id": decision.id,
                        "detail": {
                            "case_version_id": decision.case_version_id,
                            "decision": decision.decision,
                            "rationale": decision.rationale,
                        },
                        "occurred_at": decision.created_at,
                    }
                )

    # Also include owned workspaces not yet linked to a pipeline deal in an organization-wide view.
    if deal_id is None:
        unlinked = list(
            session.scalars(
                select(Workspace).where(
                    Workspace.organization_id == organization_id,
                    Workspace.id.not_in(workspace_ids) if workspace_ids else True,
                )
            )
        )
        for workspace in unlinked:
            items.append(
                {
                    "id": f"workspace:{workspace.id}",
                    "source": "workspace",
                    "category": "workflow",
                    "event_type": "workspace.created",
                    "summary": f"Workspace created: {workspace.name}",
                    "organization_id": organization_id,
                    "deal_id": None,
                    "workspace_id": workspace.id,
                    "actor_id": None,
                    "entity_type": "Workspace",
                    "entity_id": workspace.id,
                    "detail": {"status": workspace.status, "deal_type": workspace.deal_type},
                    "occurred_at": workspace.created_at,
                }
            )

    if actor_id:
        items = [item for item in items if item["actor_id"] == actor_id]
    if category:
        items = [item for item in items if item["category"] == category]
    if before:
        before_aware = _aware(before)
        items = [item for item in items if _aware(item["occurred_at"]) < before_aware]
    items.sort(key=lambda item: (_aware(item["occurred_at"]), item["id"]), reverse=True)
    return {
        "organization_id": organization_id,
        "generated_at": now_utc(),
        "total": len(items),
        "items": items[:limit],
    }


__all__ = ["ActivityNotFound", "get_timeline"]
