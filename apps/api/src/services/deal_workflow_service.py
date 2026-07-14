"""Stateful domain service for deal execution and investment-committee governance.

All mutations are tenant-scoped, audited, and committed atomically with their audit event. The
router's header identity contract is deliberately provider-neutral; a future SSO middleware can
populate the same :class:`ActorContext` after verifying its claims.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import (
    DEAL_STAGES,
    ConditionToClose,
    Deal,
    DealLedgerEntry,
    DealMilestone,
    DealStageGate,
    DealStageTransition,
    DealTask,
    DealTeamMember,
    DealWorkstream,
    DiligenceAttachment,
    DiligenceRequest,
    DiligenceResponse,
    Fund,
    ICComment,
    ICDecision,
    ICPacket,
    ICPacketExport,
    Organization,
    WorkflowAuditEvent,
)
from src.models.evidence import Evidence
from src.models.underwriting_model import UnderwritingCaseDecision, UnderwritingCaseVersion
from src.models.workspace import Workspace
from src.schemas.deal_workflow import (
    ActorContext,
    ConditionPatch,
    DealCreate,
    DealPatch,
    DiligenceAttachmentCreate,
    DiligenceRequestCreate,
    DiligenceResponseCreate,
    DiligenceReview,
    ExportRequest,
    FundCreate,
    ICCommentCreate,
    ICCommentResolve,
    ICDecisionCreate,
    ICPacketCreate,
    LedgerEntryCreate,
    LedgerEntryRevision,
    MilestoneCreate,
    MilestonePatch,
    OrganizationCreate,
    StageGateCreate,
    StageGateResolve,
    StageTransitionCreate,
    TaskCreate,
    TaskPatch,
    TeamMemberCreate,
    WorkstreamCreate,
    WorkstreamPatch,
)
from src.services.common import NotFound


class WorkflowError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class WorkflowConflict(WorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


class WorkflowForbidden(WorkflowError):
    def __init__(self, message: str = "Organization scope does not permit this operation") -> None:
        super().__init__(message, status_code=403)


DEFAULT_STAGE_GATES: dict[str, tuple[tuple[str, str], ...]] = {
    "sourcing": (("strategy_fit", "Target fits the fund mandate"),),
    "screening": (
        ("conflicts_cleared", "Conflicts and restricted-list checks are complete"),
        ("initial_thesis", "Initial investment thesis is documented"),
    ),
    "initial_review": (
        ("initial_model", "Initial returns and financing case is reviewed"),
        ("diligence_authorized", "Diligence spend and scope are authorized"),
    ),
    "diligence": (
        ("qoe_review", "Quality-of-earnings work is reviewed"),
        ("commercial_review", "Commercial diligence is reviewed"),
        ("legal_review", "Legal diligence is reviewed"),
        ("underwriting_case", "Underwriting case and debt schedule are approved for IC"),
        ("critical_issues", "Critical issues are resolved or explicitly accepted"),
    ),
    "ic_review": (("ic_materials", "IC materials and evidence manifest are complete"),),
    "signing": (
        ("definitive_documents", "Definitive documents are approved"),
        ("financing_committed", "Financing is committed"),
        ("conditions_resolved", "Conditions to close are satisfied or waived"),
    ),
}

_STAGE_ORDER = {stage: index for index, stage in enumerate(DEAL_STAGES[:-1])}
_LEAD_ROLES = {"deal_lead", "investment_partner"}
_TERMINAL_TASK_STATES = {"complete", "cancelled"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _actor_id(actor: ActorContext | None) -> str | None:
    return actor.actor_id if actor else None


def _require_actor(actor: ActorContext | None, action: str) -> str:
    actor_id = _actor_id(actor)
    if not actor_id:
        raise WorkflowError(f"{action} requires an authenticated actor", status_code=401)
    return actor_id


def _verify_org_scope(actor: ActorContext | None, organization_id: str) -> None:
    if actor and actor.organization_id and actor.organization_id != organization_id:
        raise WorkflowForbidden()


def _organization(session: Session, organization_id: str, actor: ActorContext | None = None) -> Organization:
    obj = session.get(Organization, organization_id)
    if obj is None:
        raise NotFound(f"Organization '{organization_id}' not found")
    _verify_org_scope(actor, obj.id)
    return obj


def _fund(session: Session, fund_id: str, actor: ActorContext | None = None) -> Fund:
    obj = session.get(Fund, fund_id)
    if obj is None:
        raise NotFound(f"Fund '{fund_id}' not found")
    _verify_org_scope(actor, obj.organization_id)
    return obj


def _deal(session: Session, deal_id: str, actor: ActorContext | None = None) -> Deal:
    obj = session.get(Deal, deal_id)
    if obj is None:
        raise NotFound(f"Deal '{deal_id}' not found")
    _verify_org_scope(actor, obj.organization_id)
    return obj


def _audit(
    session: Session,
    organization_id: str,
    deal_id: str | None,
    actor: ActorContext | None,
    action: str,
    entity: Any,
    detail: dict | None = None,
) -> WorkflowAuditEvent:
    event = WorkflowAuditEvent(
        organization_id=organization_id,
        deal_id=deal_id,
        actor_id=_actor_id(actor),
        actor_display_name=actor.display_name if actor else None,
        action=action,
        entity_type=type(entity).__name__,
        entity_id=entity.id,
        detail=_json_safe(detail or {}),
        request_id=actor.request_id if actor else None,
    )
    session.add(event)
    session.flush()
    # The audit event and its outbound deliveries share one transaction. This is a durable
    # outbox: a rollback can never leave a webhook queued for a workflow mutation that failed.
    from src.services import webhook_service

    webhook_service.queue_for_audit_event(session, event)
    return event


def _commit(session: Session, entity: Any) -> Any:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise WorkflowConflict("The operation conflicts with an existing workflow record") from exc
    session.refresh(entity)
    return entity


def create_organization(
    session: Session, data: OrganizationCreate, actor: ActorContext | None = None
) -> Organization:
    organization = Organization(**data.model_dump())
    session.add(organization)
    session.flush()
    _audit(session, organization.id, None, actor, "organization.created", organization)
    return _commit(session, organization)


def list_organizations(
    session: Session, actor: ActorContext | None = None
) -> list[Organization]:
    statement = select(Organization).order_by(Organization.name)
    if actor and actor.organization_id:
        statement = statement.where(Organization.id == actor.organization_id)
    return list(session.scalars(statement))


def create_fund(
    session: Session,
    organization_id: str,
    data: FundCreate,
    actor: ActorContext | None = None,
) -> Fund:
    organization = _organization(session, organization_id, actor)
    fund = Fund(organization_id=organization.id, **data.model_dump())
    session.add(fund)
    session.flush()
    _audit(session, organization.id, None, actor, "fund.created", fund)
    return _commit(session, fund)


def list_funds(
    session: Session, organization_id: str, actor: ActorContext | None = None
) -> list[Fund]:
    _organization(session, organization_id, actor)
    return list(
        session.scalars(
            select(Fund).where(Fund.organization_id == organization_id).order_by(Fund.name)
        )
    )


def _bind_workspace_to_deal_organization(
    session: Session,
    workspace_id: str,
    organization_id: str,
    *,
    current_deal_id: str | None = None,
) -> Workspace:
    """Validate a workspace link server-side and claim an unowned legacy workspace atomically."""
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    if workspace.organization_id not in {None, organization_id}:
        raise WorkflowForbidden("Workspace belongs to a different organization")
    link_query = select(Deal.id).where(Deal.workspace_id == workspace.id)
    if current_deal_id:
        link_query = link_query.where(Deal.id != current_deal_id)
    linked_deal_id = session.scalar(link_query)
    if linked_deal_id is not None:
        raise WorkflowConflict("Workspace is already linked to another deal")
    if workspace.organization_id is None:
        workspace.organization_id = organization_id
        session.flush()
    return workspace


def create_deal(
    session: Session, fund_id: str, data: DealCreate, actor: ActorContext | None = None
) -> Deal:
    fund = _fund(session, fund_id, actor)
    if data.workspace_id:
        _bind_workspace_to_deal_organization(
            session, data.workspace_id, fund.organization_id
        )
    owner_actor_id = data.owner_actor_id or _actor_id(actor)
    values = data.model_dump(exclude={"seed_default_gates", "owner_actor_id"})
    deal = Deal(
        organization_id=fund.organization_id,
        fund_id=fund.id,
        owner_actor_id=owner_actor_id,
        **values,
    )
    session.add(deal)
    session.flush()

    if data.seed_default_gates:
        for stage, gates in DEFAULT_STAGE_GATES.items():
            for code, label in gates:
                session.add(
                    DealStageGate(
                        deal_id=deal.id, stage=stage, code=code, label=label, required=True
                    )
                )
    if owner_actor_id:
        session.add(
            DealTeamMember(
                deal_id=deal.id,
                actor_id=owner_actor_id,
                display_name=(actor.display_name if actor and actor.actor_id == owner_actor_id else None)
                or owner_actor_id,
                role="deal_lead",
                added_by_actor_id=_actor_id(actor),
            )
        )
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "deal.created",
        deal,
        {"fund_id": fund.id, "default_gates_seeded": data.seed_default_gates},
    )
    return _commit(session, deal)


def get_deal(session: Session, deal_id: str, actor: ActorContext | None = None) -> Deal:
    return _deal(session, deal_id, actor)


def get_deal_by_workspace(
    session: Session, workspace_id: str, actor: ActorContext | None = None
) -> Deal:
    deal = session.scalar(select(Deal).where(Deal.workspace_id == workspace_id))
    if deal is None:
        raise NotFound(f"No deal is linked to workspace '{workspace_id}'")
    _verify_org_scope(actor, deal.organization_id)
    return deal


def list_deals(
    session: Session,
    organization_id: str,
    actor: ActorContext | None = None,
    *,
    stage: str | None = None,
    fund_id: str | None = None,
) -> list[Deal]:
    _organization(session, organization_id, actor)
    query = select(Deal).where(Deal.organization_id == organization_id)
    if stage:
        if stage not in DEAL_STAGES:
            raise WorkflowError(f"Unknown deal stage '{stage}'")
        query = query.where(Deal.stage == stage)
    if fund_id:
        fund = _fund(session, fund_id, actor)
        if fund.organization_id != organization_id:
            raise WorkflowForbidden()
        query = query.where(Deal.fund_id == fund_id)
    return list(session.scalars(query.order_by(Deal.updated_at.desc())))


def update_deal(
    session: Session,
    deal_id: str,
    data: DealPatch,
    actor: ActorContext | None = None,
) -> Deal:
    deal = _deal(session, deal_id, actor)
    if deal.version != data.expected_version:
        raise WorkflowConflict(
            f"Deal version changed (expected {data.expected_version}, current {deal.version})"
        )
    changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    if changes.get("workspace_id"):
        _bind_workspace_to_deal_organization(
            session,
            changes["workspace_id"],
            deal.organization_id,
            current_deal_id=deal.id,
        )
    for key, value in changes.items():
        setattr(deal, key, value)
    deal.version += 1
    _audit(session, deal.organization_id, deal.id, actor, "deal.updated", deal, changes)
    return _commit(session, deal)


def list_gates(
    session: Session, deal_id: str, actor: ActorContext | None = None, stage: str | None = None
) -> list[DealStageGate]:
    _deal(session, deal_id, actor)
    query = select(DealStageGate).where(DealStageGate.deal_id == deal_id)
    if stage:
        query = query.where(DealStageGate.stage == stage)
    return list(session.scalars(query.order_by(DealStageGate.stage, DealStageGate.code)))


def create_gate(
    session: Session,
    deal_id: str,
    data: StageGateCreate,
    actor: ActorContext | None = None,
) -> DealStageGate:
    deal = _deal(session, deal_id, actor)
    gate = DealStageGate(deal_id=deal.id, **data.model_dump())
    session.add(gate)
    session.flush()
    _audit(session, deal.organization_id, deal.id, actor, "stage_gate.created", gate)
    return _commit(session, gate)


def resolve_gate(
    session: Session,
    gate_id: str,
    data: StageGateResolve,
    actor: ActorContext | None = None,
) -> DealStageGate:
    gate = session.get(DealStageGate, gate_id)
    if gate is None:
        raise NotFound(f"Stage gate '{gate_id}' not found")
    deal = _deal(session, gate.deal_id, actor)
    resolver = _require_actor(actor, "Stage-gate resolution")
    gate.status = data.status
    gate.evidence_refs = data.evidence_refs
    gate.resolution_note = data.resolution_note
    gate.resolved_by_actor_id = resolver
    gate.resolved_at = now_utc()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "stage_gate.resolved",
        gate,
        {"status": gate.status, "evidence_refs": gate.evidence_refs},
    )
    return _commit(session, gate)


def transition_deal(
    session: Session,
    deal_id: str,
    data: StageTransitionCreate,
    actor: ActorContext | None = None,
) -> DealStageTransition:
    deal = _deal(session, deal_id, actor)
    transition_actor = _require_actor(actor, "Deal-stage transition")
    destination = data.to_stage
    if destination == deal.stage:
        raise WorkflowConflict(f"Deal is already in '{destination}'")
    if destination == "declined":
        if not data.rationale:
            raise WorkflowError("Declining a deal requires a rationale")
    else:
        if deal.stage in {"closed", "declined"}:
            raise WorkflowConflict(f"Cannot advance a {deal.stage} deal")
        expected = DEAL_STAGES[_STAGE_ORDER[deal.stage] + 1]
        if destination != expected:
            raise WorkflowConflict(
                f"Invalid stage transition '{deal.stage}' -> '{destination}'; next stage is '{expected}'"
            )
        unresolved = list(
            session.scalars(
                select(DealStageGate).where(
                    DealStageGate.deal_id == deal.id,
                    DealStageGate.stage == deal.stage,
                    DealStageGate.required.is_(True),
                    DealStageGate.status == "pending",
                )
            )
        )
        if unresolved:
            labels = ", ".join(g.label for g in unresolved)
            raise WorkflowConflict(f"Required stage gates are unresolved: {labels}")

    sequence = (
        session.scalar(
            select(func.max(DealStageTransition.sequence)).where(
                DealStageTransition.deal_id == deal.id
            )
        )
        or 0
    ) + 1
    transition = DealStageTransition(
        deal_id=deal.id,
        sequence=sequence,
        from_stage=deal.stage,
        to_stage=destination,
        rationale=data.rationale,
        actor_id=transition_actor,
    )
    session.add(transition)
    session.flush()
    previous = deal.stage
    deal.stage = destination
    deal.status = "declined" if destination == "declined" else "closed" if destination == "closed" else "active"
    deal.version += 1
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "deal.stage_transitioned",
        transition,
        {"from": previous, "to": destination},
    )
    return _commit(session, transition)


def add_team_member(
    session: Session,
    deal_id: str,
    data: TeamMemberCreate,
    actor: ActorContext | None = None,
) -> DealTeamMember:
    deal = _deal(session, deal_id, actor)
    member = session.scalar(
        select(DealTeamMember).where(
            DealTeamMember.deal_id == deal.id, DealTeamMember.actor_id == data.actor_id
        )
    )
    action = "deal_team.updated"
    if member is None:
        member = DealTeamMember(
            deal_id=deal.id, added_by_actor_id=_actor_id(actor), **data.model_dump()
        )
        session.add(member)
        session.flush()
        action = "deal_team.added"
    else:
        member.display_name = data.display_name
        member.email = data.email
        member.role = data.role
        member.is_active = True
    _audit(session, deal.organization_id, deal.id, actor, action, member, {"role": member.role})
    return _commit(session, member)


def list_team(
    session: Session, deal_id: str, actor: ActorContext | None = None
) -> list[DealTeamMember]:
    _deal(session, deal_id, actor)
    return list(
        session.scalars(
            select(DealTeamMember)
            .where(DealTeamMember.deal_id == deal_id, DealTeamMember.is_active.is_(True))
            .order_by(DealTeamMember.role, DealTeamMember.display_name)
        )
    )


def create_workstream(
    session: Session,
    deal_id: str,
    data: WorkstreamCreate,
    actor: ActorContext | None = None,
) -> DealWorkstream:
    deal = _deal(session, deal_id, actor)
    workstream = DealWorkstream(deal_id=deal.id, **data.model_dump())
    session.add(workstream)
    session.flush()
    _audit(session, deal.organization_id, deal.id, actor, "workstream.created", workstream)
    return _commit(session, workstream)


def list_workstreams(
    session: Session, deal_id: str, actor: ActorContext | None = None
) -> list[DealWorkstream]:
    _deal(session, deal_id, actor)
    return list(
        session.scalars(
            select(DealWorkstream)
            .where(DealWorkstream.deal_id == deal_id)
            .order_by(DealWorkstream.label)
        )
    )


def update_workstream(
    session: Session,
    workstream_id: str,
    data: WorkstreamPatch,
    actor: ActorContext | None = None,
) -> DealWorkstream:
    workstream = session.get(DealWorkstream, workstream_id)
    if workstream is None:
        raise NotFound(f"Workstream '{workstream_id}' not found")
    deal = _deal(session, workstream.deal_id, actor)
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(workstream, key, value)
    _audit(session, deal.organization_id, deal.id, actor, "workstream.updated", workstream, changes)
    return _commit(session, workstream)


def _validate_deal_child(session: Session, model: type, entity_id: str | None, deal_id: str, label: str):
    if not entity_id:
        return None
    entity = session.get(model, entity_id)
    if entity is None or entity.deal_id != deal_id:
        raise WorkflowError(f"{label} '{entity_id}' does not belong to deal '{deal_id}'")
    return entity


def create_milestone(
    session: Session,
    deal_id: str,
    data: MilestoneCreate,
    actor: ActorContext | None = None,
) -> DealMilestone:
    deal = _deal(session, deal_id, actor)
    _validate_deal_child(session, DealWorkstream, data.workstream_id, deal.id, "Workstream")
    milestone = DealMilestone(deal_id=deal.id, **data.model_dump())
    session.add(milestone)
    session.flush()
    _audit(session, deal.organization_id, deal.id, actor, "milestone.created", milestone)
    return _commit(session, milestone)


def update_milestone(
    session: Session,
    milestone_id: str,
    data: MilestonePatch,
    actor: ActorContext | None = None,
) -> DealMilestone:
    milestone = session.get(DealMilestone, milestone_id)
    if milestone is None:
        raise NotFound(f"Milestone '{milestone_id}' not found")
    deal = _deal(session, milestone.deal_id, actor)
    if milestone.status in _TERMINAL_TASK_STATES and data.status and data.status != milestone.status:
        raise WorkflowConflict(f"Cannot reopen {milestone.status} milestone")
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(milestone, key, value)
    if data.status == "complete":
        milestone.completed_at = now_utc()
        milestone.completed_by_actor_id = _actor_id(actor)
    _audit(session, deal.organization_id, deal.id, actor, "milestone.updated", milestone, changes)
    return _commit(session, milestone)


def list_milestones(
    session: Session, deal_id: str, actor: ActorContext | None = None
) -> list[DealMilestone]:
    _deal(session, deal_id, actor)
    return list(
        session.scalars(
            select(DealMilestone)
            .where(DealMilestone.deal_id == deal_id)
            .order_by(DealMilestone.due_date, DealMilestone.created_at)
        )
    )


def create_task(
    session: Session, deal_id: str, data: TaskCreate, actor: ActorContext | None = None
) -> DealTask:
    deal = _deal(session, deal_id, actor)
    _validate_deal_child(session, DealWorkstream, data.workstream_id, deal.id, "Workstream")
    _validate_deal_child(session, DealMilestone, data.milestone_id, deal.id, "Milestone")
    _validate_deal_child(session, DealTask, data.parent_task_id, deal.id, "Parent task")
    for dependency_id in data.dependency_task_ids:
        _validate_deal_child(session, DealTask, dependency_id, deal.id, "Dependency task")
    task = DealTask(deal_id=deal.id, **data.model_dump())
    session.add(task)
    session.flush()
    _audit(session, deal.organization_id, deal.id, actor, "task.created", task)
    return _commit(session, task)


def update_task(
    session: Session, task_id: str, data: TaskPatch, actor: ActorContext | None = None
) -> DealTask:
    task = session.get(DealTask, task_id)
    if task is None:
        raise NotFound(f"Task '{task_id}' not found")
    deal = _deal(session, task.deal_id, actor)
    if task.status in _TERMINAL_TASK_STATES and data.status and data.status != task.status:
        raise WorkflowConflict(f"Cannot reopen {task.status} task")
    if data.status == "complete" and task.dependency_task_ids:
        completed = set(
            session.scalars(
                select(DealTask.id).where(
                    DealTask.id.in_(task.dependency_task_ids), DealTask.status == "complete"
                )
            )
        )
        missing = set(task.dependency_task_ids) - completed
        if missing:
            raise WorkflowConflict(
                "Task dependencies must be complete first: " + ", ".join(sorted(missing))
            )
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(task, key, value)
    if data.status == "complete":
        task.completed_at = now_utc()
        task.completed_by_actor_id = _actor_id(actor)
        task.blocked_reason = ""
    _audit(session, deal.organization_id, deal.id, actor, "task.updated", task, changes)
    return _commit(session, task)


def list_tasks(
    session: Session,
    deal_id: str,
    actor: ActorContext | None = None,
    *,
    status: str | None = None,
) -> list[DealTask]:
    _deal(session, deal_id, actor)
    query = select(DealTask).where(DealTask.deal_id == deal_id)
    if status:
        query = query.where(DealTask.status == status)
    return list(session.scalars(query.order_by(DealTask.due_date, DealTask.created_at)))


def create_diligence_request(
    session: Session,
    deal_id: str,
    data: DiligenceRequestCreate,
    actor: ActorContext | None = None,
) -> DiligenceRequest:
    deal = _deal(session, deal_id, actor)
    _validate_deal_child(session, DealWorkstream, data.workstream_id, deal.id, "Workstream")
    number = (
        session.scalar(
            select(func.max(DiligenceRequest.request_number)).where(
                DiligenceRequest.deal_id == deal.id
            )
        )
        or 0
    ) + 1
    values = data.model_dump(exclude={"send_now"})
    request = DiligenceRequest(
        deal_id=deal.id,
        request_number=number,
        status="requested" if data.send_now else "draft",
        requested_at=now_utc() if data.send_now else None,
        **values,
    )
    session.add(request)
    session.flush()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "diligence_request.created",
        request,
        {"request_number": number, "sent": data.send_now},
    )
    return _commit(session, request)


def list_diligence_requests(
    session: Session,
    deal_id: str,
    actor: ActorContext | None = None,
    *,
    status: str | None = None,
) -> list[DiligenceRequest]:
    _deal(session, deal_id, actor)
    query = select(DiligenceRequest).where(DiligenceRequest.deal_id == deal_id)
    if status:
        query = query.where(DiligenceRequest.status == status)
    return list(session.scalars(query.order_by(DiligenceRequest.request_number)))


def send_diligence_request(
    session: Session, request_id: str, actor: ActorContext | None = None
) -> DiligenceRequest:
    request = session.get(DiligenceRequest, request_id)
    if request is None:
        raise NotFound(f"Diligence request '{request_id}' not found")
    deal = _deal(session, request.deal_id, actor)
    if request.status not in {"draft", "rejected"}:
        raise WorkflowConflict(f"Cannot send a request in '{request.status}' state")
    request.status = "requested"
    request.requested_at = now_utc()
    request.review_note = ""
    _audit(session, deal.organization_id, deal.id, actor, "diligence_request.sent", request)
    return _commit(session, request)


def add_diligence_response(
    session: Session,
    request_id: str,
    data: DiligenceResponseCreate,
    actor: ActorContext | None = None,
) -> DiligenceResponse:
    request = session.get(DiligenceRequest, request_id)
    if request is None:
        raise NotFound(f"Diligence request '{request_id}' not found")
    deal = _deal(session, request.deal_id, actor)
    respondent = _require_actor(actor, "Diligence response")
    if request.status not in {"requested", "responded", "under_review", "rejected"}:
        raise WorkflowConflict(f"Cannot respond to a request in '{request.status}' state")
    sequence = (
        session.scalar(
            select(func.max(DiligenceResponse.sequence)).where(
                DiligenceResponse.request_id == request.id
            )
        )
        or 0
    ) + 1
    response = DiligenceResponse(
        request_id=request.id,
        sequence=sequence,
        response_text=data.response_text,
        responded_by_actor_id=respondent,
    )
    session.add(response)
    session.flush()
    request.status = "responded"
    request.last_response_at = response.submitted_at
    request.respondent_actor_id = request.respondent_actor_id or respondent
    request.accepted_at = None
    request.accepted_by_actor_id = None
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "diligence_response.added",
        response,
        {"request_id": request.id, "sequence": sequence},
    )
    return _commit(session, response)


def add_diligence_attachment(
    session: Session,
    request_id: str,
    data: DiligenceAttachmentCreate,
    actor: ActorContext | None = None,
) -> DiligenceAttachment:
    request = session.get(DiligenceRequest, request_id)
    if request is None:
        raise NotFound(f"Diligence request '{request_id}' not found")
    deal = _deal(session, request.deal_id, actor)
    if data.response_id:
        response = session.get(DiligenceResponse, data.response_id)
        if response is None or response.request_id != request.id:
            raise WorkflowError("Attachment response does not belong to this diligence request")
    attachment = DiligenceAttachment(
        request_id=request.id, uploaded_by_actor_id=_actor_id(actor), **data.model_dump()
    )
    session.add(attachment)
    session.flush()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "diligence_attachment.added",
        attachment,
        {"source_hash": attachment.source_hash, "version": attachment.version},
    )
    return _commit(session, attachment)


def review_diligence_request(
    session: Session,
    request_id: str,
    data: DiligenceReview,
    actor: ActorContext | None = None,
) -> DiligenceRequest:
    request = session.get(DiligenceRequest, request_id)
    if request is None:
        raise NotFound(f"Diligence request '{request_id}' not found")
    deal = _deal(session, request.deal_id, actor)
    if request.status not in {"responded", "under_review"}:
        raise WorkflowConflict(f"Request in '{request.status}' state is not ready for review")
    latest = session.scalar(
        select(DiligenceResponse)
        .where(DiligenceResponse.request_id == request.id)
        .order_by(DiligenceResponse.sequence.desc())
        .limit(1)
    )
    if latest is None:
        raise WorkflowConflict("A request cannot be reviewed before it has a response")
    reviewer = _require_actor(actor, "Diligence review")
    if reviewer == latest.responded_by_actor_id:
        raise WorkflowConflict("The response author cannot accept their own response")
    request.review_note = data.note
    if data.action == "accept":
        request.status = "accepted"
        request.accepted_at = now_utc()
        request.accepted_by_actor_id = reviewer
    else:
        request.status = "rejected"
        request.accepted_at = None
        request.accepted_by_actor_id = None
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        f"diligence_request.{data.action}ed",
        request,
        {"response_id": latest.id, "note": data.note},
    )
    return _commit(session, request)


def create_ledger_entry(
    session: Session,
    deal_id: str,
    data: LedgerEntryCreate,
    actor: ActorContext | None = None,
) -> DealLedgerEntry:
    deal = _deal(session, deal_id, actor)
    entry = DealLedgerEntry(
        deal_id=deal.id,
        created_by_actor_id=_actor_id(actor),
        **data.model_dump(),
    )
    session.add(entry)
    session.flush()
    entry.root_entry_id = entry.id
    _audit(session, deal.organization_id, deal.id, actor, "ledger_entry.created", entry)
    return _commit(session, entry)


def revise_ledger_entry(
    session: Session,
    entry_id: str,
    data: LedgerEntryRevision,
    actor: ActorContext | None = None,
) -> DealLedgerEntry:
    prior = session.get(DealLedgerEntry, entry_id)
    if prior is None:
        raise NotFound(f"Ledger entry '{entry_id}' not found")
    deal = _deal(session, prior.deal_id, actor)
    if prior.status == "superseded":
        raise WorkflowConflict("A superseded ledger revision cannot be revised")
    values = {
        "entry_type": prior.entry_type,
        "title": prior.title,
        "description": prior.description,
        "status": prior.status,
        "severity": prior.severity,
        "owner_actor_id": prior.owner_actor_id,
        "evidence_refs": prior.evidence_refs,
        "related_artifact_ids": prior.related_artifact_ids,
    }
    values.update(data.model_dump(exclude_unset=True))
    revision = DealLedgerEntry(
        deal_id=prior.deal_id,
        root_entry_id=prior.root_entry_id or prior.id,
        supersedes_entry_id=prior.id,
        version=prior.version + 1,
        created_by_actor_id=_actor_id(actor),
        **values,
    )
    prior.status = "superseded"
    session.add(revision)
    session.flush()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ledger_entry.revised",
        revision,
        {"supersedes_entry_id": prior.id, "version": revision.version},
    )
    return _commit(session, revision)


def list_ledger_entries(
    session: Session,
    deal_id: str,
    actor: ActorContext | None = None,
    *,
    include_superseded: bool = False,
    entry_type: str | None = None,
) -> list[DealLedgerEntry]:
    _deal(session, deal_id, actor)
    query = select(DealLedgerEntry).where(DealLedgerEntry.deal_id == deal_id)
    if not include_superseded:
        query = query.where(DealLedgerEntry.status != "superseded")
    if entry_type:
        query = query.where(DealLedgerEntry.entry_type == entry_type)
    return list(session.scalars(query.order_by(DealLedgerEntry.updated_at.desc())))


def _latest_case_decision(
    session: Session, case_version_id: str
) -> UnderwritingCaseDecision | None:
    return session.scalar(
        select(UnderwritingCaseDecision)
        .where(UnderwritingCaseDecision.case_version_id == case_version_id)
        .order_by(
            UnderwritingCaseDecision.created_at.desc(),
            UnderwritingCaseDecision.id.desc(),
        )
        .limit(1)
    )


def _ledger_snapshot(entry: DealLedgerEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "root_entry_id": entry.root_entry_id,
        "version": entry.version,
        "entry_type": entry.entry_type,
        "title": entry.title,
        "description": entry.description,
        "status": entry.status,
        "severity": entry.severity,
        "owner_actor_id": entry.owner_actor_id,
        "evidence_refs": entry.evidence_refs,
        "related_artifact_ids": entry.related_artifact_ids,
    }


def _workspace_evidence_manifest(
    session: Session, deal: Deal, refs: list[str]
) -> list[dict[str, Any]]:
    if not refs:
        return []
    if not deal.workspace_id:
        raise WorkflowError("workspace_evidence_refs require a deal-linked workspace")
    rows = list(
        session.scalars(
            select(Evidence).where(
                Evidence.workspace_id == deal.workspace_id,
                Evidence.ref.in_(refs),
            )
        )
    )
    by_ref = {row.ref: row for row in rows}
    missing = [ref for ref in refs if ref not in by_ref]
    if missing:
        raise WorkflowError(f"Unknown workspace evidence refs: {', '.join(missing)}")
    manifest: list[dict[str, Any]] = []
    for ref in refs:
        row = by_ref[ref]
        entry = {
            "kind": "workspace_evidence",
            "evidence_id": row.id,
            "ref": row.ref,
            "claim": row.claim,
            "claim_type": row.claim_type,
            "source_name": row.source_name,
            "source_type": row.source_type,
            "source_url": row.source_url,
            "source_date": row.source_date,
            "source_section": row.source_section,
            "evidence_text": row.evidence_text,
            "confidence": row.confidence,
        }
        entry["manifest_hash"] = _sha256(entry)
        manifest.append(entry)
    return manifest


def _governed_packet_payload(
    session: Session,
    deal: Deal,
    data: ICPacketCreate,
    actor: ActorContext | None,
) -> dict[str, Any]:
    if not deal.workspace_id:
        raise WorkflowError("Governed IC assembly requires a deal-linked workspace")
    case_rows = list(
        session.scalars(
            select(UnderwritingCaseVersion).where(
                UnderwritingCaseVersion.id.in_(data.case_version_ids)
            )
        )
    )
    by_id = {row.id: row for row in case_rows}
    missing_cases = [case_id for case_id in data.case_version_ids if case_id not in by_id]
    if missing_cases:
        raise WorkflowError(f"Unknown underwriting case versions: {', '.join(missing_cases)}")
    wrong_workspace = [
        row.id for row in case_rows if row.workspace_id != deal.workspace_id
    ]
    if wrong_workspace:
        raise WorkflowForbidden("Underwriting case versions do not belong to this deal")

    scenario_cases: list[dict[str, Any]] = []
    model_cases: list[dict[str, Any]] = []
    case_bound_claim_ids: list[str] = []
    for case_id in data.case_version_ids:
        row = by_id[case_id]
        case_bound_claim_ids.extend(row.approved_claim_ids or [])
        decision = _latest_case_decision(session, row.id)
        decision_snapshot = (
            {
                "id": decision.id,
                "decision": decision.decision,
                "actor": decision.actor,
                "rationale": decision.rationale,
                "created_at": _utc_iso(decision.created_at),
            }
            if decision
            else None
        )
        scenario_cases.append(
            {
                "id": row.id,
                "case_key": row.case_key,
                "label": row.label,
                "version": row.version,
                "parent_version_id": row.parent_version_id,
                "schema_version": row.schema_version,
                "assumptions": row.assumptions,
                "approved_claim_ids": row.approved_claim_ids,
                "approved_claim_manifest": row.approved_claim_manifest,
                "claim_manifest_hash": row.claim_manifest_hash,
                "input_hash": row.input_hash,
                "created_by": row.created_by,
                "change_note": row.change_note,
                "latest_decision": decision_snapshot,
            }
        )
        model_cases.append(
            {
                "id": row.id,
                "case_key": row.case_key,
                "version": row.version,
                "result": row.result,
                "output_hash": row.output_hash,
            }
        )

    from src.services import deal_intelligence_service

    approved_claim_ids = list(
        dict.fromkeys([*case_bound_claim_ids, *data.approved_claim_ids])
    )
    try:
        private_evidence = deal_intelligence_service.approved_claim_manifest(
            session, deal.id, approved_claim_ids, actor
        )
    except deal_intelligence_service.IntelligenceError as exc:
        raise WorkflowError(exc.message, status_code=exc.status_code) from exc
    evidence_manifest = _workspace_evidence_manifest(
        session, deal, data.workspace_evidence_refs
    ) + private_evidence
    ledger = list_ledger_entries(session, deal.id, actor, include_superseded=False)
    thesis = [_ledger_snapshot(item) for item in ledger if item.entry_type == "thesis"]
    risks = [
        _ledger_snapshot(item) for item in ledger if item.entry_type in {"issue", "risk"}
    ]
    assembly = {
        "mode": "governed",
        "case_version_ids": data.case_version_ids,
        "approved_claim_ids": approved_claim_ids,
        "case_bound_approved_claim_ids": list(dict.fromkeys(case_bound_claim_ids)),
        "requested_approved_claim_ids": data.approved_claim_ids,
        "workspace_evidence_refs": data.workspace_evidence_refs,
    }
    return _json_safe({
        "title": data.title,
        "scenario_snapshot": {"_assembly": assembly, "cases": scenario_cases},
        "model_snapshot": {
            "assembly_hash": _sha256(assembly),
            "cases": model_cases,
        },
        "evidence_manifest": evidence_manifest,
        "thesis_snapshot": thesis,
        "risk_snapshot": risks,
        "decision_request": _json_safe(data.decision_request),
    })


def _packet_payload(
    session: Session,
    deal: Deal,
    data: ICPacketCreate,
    actor: ActorContext | None,
) -> dict[str, Any]:
    return _governed_packet_payload(session, deal, data, actor)


def create_ic_packet(
    session: Session,
    deal_id: str,
    data: ICPacketCreate,
    actor: ActorContext | None = None,
) -> ICPacket:
    deal = _deal(session, deal_id, actor)
    previous: ICPacket | None = None
    if data.previous_packet_id:
        previous = session.get(ICPacket, data.previous_packet_id)
        if previous is None or previous.deal_id != deal.id:
            raise WorkflowError("previous_packet_id must reference the same deal")
    else:
        previous = session.scalar(
            select(ICPacket).where(ICPacket.deal_id == deal.id).order_by(ICPacket.version.desc())
        )
    version = (
        session.scalar(select(func.max(ICPacket.version)).where(ICPacket.deal_id == deal.id)) or 0
    ) + 1
    payload = _packet_payload(session, deal, data, actor)
    packet = ICPacket(
        deal_id=deal.id,
        version=version,
        previous_packet_id=previous.id if previous else None,
        content_hash=_sha256(payload),
        created_by_actor_id=_actor_id(actor),
        **payload,
    )
    session.add(packet)
    session.flush()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_packet.created",
        packet,
        {"version": version, "content_hash": packet.content_hash},
    )
    return _commit(session, packet)


def _packet_content_payload(packet: ICPacket) -> dict[str, Any]:
    return {
        "title": packet.title,
        "scenario_snapshot": packet.scenario_snapshot,
        "model_snapshot": packet.model_snapshot,
        "evidence_manifest": packet.evidence_manifest,
        "thesis_snapshot": packet.thesis_snapshot,
        "risk_snapshot": packet.risk_snapshot,
        "decision_request": packet.decision_request,
    }


def _require_governed_packet(packet: ICPacket, action: str) -> None:
    if packet.scenario_snapshot.get("_assembly", {}).get("mode") != "governed":
        raise WorkflowConflict(
            f"Cannot {action} a legacy client-snapshot packet; create a governed packet from "
            "approved model-of-record IDs"
        )


def _packet_hash_resolves(packet: ICPacket) -> bool:
    return _sha256(_packet_content_payload(packet)) == packet.content_hash


def get_ic_packet(
    session: Session, packet_id: str, actor: ActorContext | None = None
) -> ICPacket:
    packet = session.get(ICPacket, packet_id)
    if packet is None:
        raise NotFound(f"IC packet '{packet_id}' not found")
    _deal(session, packet.deal_id, actor)
    if packet.status != "draft" and not _packet_hash_resolves(packet):
        raise WorkflowConflict("The frozen IC packet content hash no longer resolves")
    return packet


def list_ic_packets(
    session: Session, deal_id: str, actor: ActorContext | None = None
) -> list[ICPacket]:
    _deal(session, deal_id, actor)
    return list(
        session.scalars(
            select(ICPacket).where(ICPacket.deal_id == deal_id).order_by(ICPacket.version.desc())
        )
    )


def _governed_sources_current(
    session: Session,
    deal: Deal,
    packet: ICPacket,
    actor: ActorContext | None,
) -> tuple[bool, list[str]]:
    assembly = packet.scenario_snapshot.get("_assembly", {})
    if assembly.get("mode") != "governed":
        return False, [packet.id]
    stale: list[str] = []
    scenario_by_id = {
        item.get("id"): item for item in packet.scenario_snapshot.get("cases", [])
    }
    model_by_id = {item.get("id"): item for item in packet.model_snapshot.get("cases", [])}
    for case_id in assembly.get("case_version_ids", []):
        case = session.get(UnderwritingCaseVersion, case_id)
        scenario = scenario_by_id.get(case_id)
        model = model_by_id.get(case_id)
        decision = _latest_case_decision(session, case_id)
        if (
            case is None
            or case.workspace_id != deal.workspace_id
            or scenario is None
            or model is None
            or scenario.get("input_hash") != case.input_hash
            or model.get("output_hash") != case.output_hash
            or decision is None
            or decision.decision != "approved"
            or (scenario.get("latest_decision") or {}).get("id") != decision.id
        ):
            stale.append(case_id)

    expected_private = {
        item.get("claim_id"): item.get("manifest_hash")
        for item in packet.evidence_manifest
        if item.get("kind") == "approved_private_claim"
    }
    from src.services import deal_intelligence_service

    try:
        current_private = deal_intelligence_service.approved_claim_manifest(
            session,
            deal.id,
            list(assembly.get("approved_claim_ids", [])),
            actor,
        )
        current_private_hashes = {
            item["claim_id"]: item["manifest_hash"] for item in current_private
        }
        if expected_private != current_private_hashes:
            stale.extend(assembly.get("approved_claim_ids", []))
    except deal_intelligence_service.IntelligenceError:
        stale.extend(assembly.get("approved_claim_ids", []))

    expected_workspace = {
        item.get("ref"): item.get("manifest_hash")
        for item in packet.evidence_manifest
        if item.get("kind") == "workspace_evidence"
    }
    try:
        current_workspace = _workspace_evidence_manifest(
            session, deal, list(assembly.get("workspace_evidence_refs", []))
        )
        current_workspace_hashes = {
            item["ref"]: item["manifest_hash"] for item in current_workspace
        }
        if expected_workspace != current_workspace_hashes:
            stale.extend(assembly.get("workspace_evidence_refs", []))
    except WorkflowError:
        stale.extend(assembly.get("workspace_evidence_refs", []))
    return not stale, list(dict.fromkeys(stale))


def _governed_packet_bindings_resolve(
    session: Session, deal: Deal, packet: ICPacket
) -> tuple[bool, list[str], bool, list[str]]:
    """Verify frozen governed case/evidence snapshots against their immutable source rows."""
    assembly = packet.scenario_snapshot.get("_assembly", {})
    if assembly.get("mode") != "governed":
        return True, [], True, []

    case_stale: list[str] = []
    expected_case_ids = list(assembly.get("case_version_ids", []))
    scenario_rows = packet.scenario_snapshot.get("cases", [])
    model_rows = packet.model_snapshot.get("cases", [])
    scenario_by_id = {item.get("id"): item for item in scenario_rows}
    model_by_id = {item.get("id"): item for item in model_rows}
    if [item.get("id") for item in scenario_rows] != expected_case_ids:
        case_stale.extend(expected_case_ids or [packet.id])
    if [item.get("id") for item in model_rows] != expected_case_ids:
        case_stale.extend(expected_case_ids or [packet.id])
    for case_id in expected_case_ids:
        case = session.get(UnderwritingCaseVersion, case_id)
        expected_scenario = scenario_by_id.get(case_id)
        expected_model = model_by_id.get(case_id)
        decision_snapshot = (expected_scenario or {}).get("latest_decision")
        decision = (
            session.get(UnderwritingCaseDecision, decision_snapshot.get("id"))
            if isinstance(decision_snapshot, dict) and decision_snapshot.get("id")
            else None
        )
        if case is None or case.workspace_id != deal.workspace_id:
            case_stale.append(case_id)
            continue
        current_decision = (
            {
                "id": decision.id,
                "decision": decision.decision,
                "actor": decision.actor,
                "rationale": decision.rationale,
                "created_at": _utc_iso(decision.created_at),
            }
            if decision is not None and decision.case_version_id == case.id
            else None
        )
        current_scenario = _json_safe(
            {
                "id": case.id,
                "case_key": case.case_key,
                "label": case.label,
                "version": case.version,
                "parent_version_id": case.parent_version_id,
                "schema_version": case.schema_version,
                "assumptions": case.assumptions,
                "approved_claim_ids": case.approved_claim_ids,
                "approved_claim_manifest": case.approved_claim_manifest,
                "claim_manifest_hash": case.claim_manifest_hash,
                "input_hash": case.input_hash,
                "created_by": case.created_by,
                "change_note": case.change_note,
                "latest_decision": current_decision,
            }
        )
        current_model = _json_safe(
            {
                "id": case.id,
                "case_key": case.case_key,
                "version": case.version,
                "result": case.result,
                "output_hash": case.output_hash,
            }
        )
        if expected_scenario != current_scenario or expected_model != current_model:
            case_stale.append(case_id)

    evidence_stale: list[str] = []
    expected_workspace_refs = list(assembly.get("workspace_evidence_refs", []))
    workspace_entries = [
        item for item in packet.evidence_manifest if item.get("kind") == "workspace_evidence"
    ]
    if [item.get("ref") for item in workspace_entries] != expected_workspace_refs:
        evidence_stale.extend(expected_workspace_refs or [packet.id])
    else:
        try:
            current_workspace_entries = _workspace_evidence_manifest(
                session, deal, expected_workspace_refs
            )
            if workspace_entries != current_workspace_entries:
                evidence_stale.extend(expected_workspace_refs or [packet.id])
        except WorkflowError:
            evidence_stale.extend(expected_workspace_refs or [packet.id])

    from src.models.deal_intelligence import (
        ClaimReviewEvent,
        DataRoomChunk,
        DataRoomDocument,
        StructuredClaim,
    )

    expected_claim_ids = list(assembly.get("approved_claim_ids", []))
    private_entries = [
        item
        for item in packet.evidence_manifest
        if item.get("kind") == "approved_private_claim"
    ]
    if [item.get("claim_id") for item in private_entries] != expected_claim_ids:
        evidence_stale.extend(expected_claim_ids or [packet.id])
    for expected in private_entries:
        claim_id = expected.get("claim_id")
        claim = session.get(StructuredClaim, claim_id) if claim_id else None
        approval_data = expected.get("approval") or {}
        source_data = expected.get("source") or {}
        evidence_data = expected.get("governed_evidence") or {}
        approval = (
            session.get(ClaimReviewEvent, approval_data.get("review_event_id"))
            if approval_data.get("review_event_id")
            else None
        )
        document = (
            session.get(DataRoomDocument, source_data.get("document_id"))
            if source_data.get("document_id")
            else None
        )
        chunk = (
            session.get(DataRoomChunk, source_data.get("chunk_id"))
            if source_data.get("chunk_id")
            else None
        )
        governed_evidence = (
            session.get(Evidence, evidence_data.get("evidence_id"))
            if evidence_data.get("evidence_id")
            else None
        )
        if (
            claim is None
            or claim.deal_id != deal.id
            or claim.review_status != "approved"
            or approval is None
            or approval.to_claim_id != claim.id
            or approval.action != "approve"
            or document is None
            or document.id != claim.document_id
            or document.deal_id != deal.id
            or chunk is None
            or chunk.id != claim.chunk_id
            or chunk.document_id != document.id
            or governed_evidence is None
            or governed_evidence.workspace_id != deal.workspace_id
        ):
            evidence_stale.append(claim_id or packet.id)
            continue
        span = claim.source_span or {}
        start, end, quoted = span.get("start"), span.get("end"), span.get("text", "")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not quoted
            or chunk.text[start:end] != quoted
        ):
            evidence_stale.append(claim.id)
            continue
        current = {
            "kind": "approved_private_claim",
            "claim_id": claim.id,
            "logical_claim_id": claim.logical_claim_id,
            "revision": claim.revision,
            "category": claim.category,
            "field_name": claim.field_name,
            "value_text": claim.value_text,
            "value_number": claim.value_number,
            "unit": claim.unit,
            "period": claim.period,
            "currency": claim.currency,
            "confidence": claim.confidence,
            "approval": {
                "review_event_id": approval.id,
                "reviewer_actor_id": approval.reviewer_actor_id,
                "approved_at": _utc_iso(approval.created_at),
            },
            "governed_evidence": {
                "evidence_id": governed_evidence.id,
                "ref": governed_evidence.ref,
                "workspace_id": governed_evidence.workspace_id,
                "claim": governed_evidence.claim,
                "claim_type": governed_evidence.claim_type,
                "source_name": governed_evidence.source_name,
                "source_type": governed_evidence.source_type,
                "source_url": governed_evidence.source_url,
                "source_date": governed_evidence.source_date,
                "source_section": governed_evidence.source_section,
                "evidence_text": governed_evidence.evidence_text,
                "confidence": governed_evidence.confidence,
                "agent_name": governed_evidence.agent_name,
            },
            "source": {
                "document_id": document.id,
                "logical_document_id": document.logical_document_id,
                "document_version": document.version,
                "filename": document.filename,
                "document_sha256": document.sha256,
                "chunk_id": chunk.id,
                "chunk_hash": chunk.content_hash,
                "locator": chunk.locator,
                "span": claim.source_span,
            },
        }
        current["manifest_hash"] = _sha256(current)
        if expected != _json_safe(current):
            evidence_stale.append(claim.id)

    return (
        not case_stale,
        list(dict.fromkeys(case_stale)),
        not evidence_stale,
        list(dict.fromkeys(evidence_stale)),
    )


def evaluate_ic_readiness(
    session: Session, packet_id: str, actor: ActorContext | None = None
) -> dict:
    packet = get_ic_packet(session, packet_id, actor)
    deal = _deal(session, packet.deal_id, actor)
    if packet.status != "draft":
        return packet.readiness_snapshot

    members = list(
        session.scalars(
            select(DealTeamMember).where(
                DealTeamMember.deal_id == deal.id, DealTeamMember.is_active.is_(True)
            )
        )
    )
    lead_ids = [member.id for member in members if member.role in _LEAD_ROLES]
    unresolved_gates = list(
        session.scalars(
            select(DealStageGate).where(
                DealStageGate.deal_id == deal.id,
                DealStageGate.required.is_(True),
                DealStageGate.status == "pending",
                DealStageGate.stage.in_(["sourcing", "screening", "initial_review", "diligence", "ic_review"]),
            )
        )
    )
    blocking_requests = list(
        session.scalars(
            select(DiligenceRequest).where(
                DiligenceRequest.deal_id == deal.id,
                DiligenceRequest.priority.in_(["high", "critical"]),
                DiligenceRequest.status.not_in(["accepted", "closed"]),
            )
        )
    )
    critical_issues = list(
        session.scalars(
            select(DealLedgerEntry).where(
                DealLedgerEntry.deal_id == deal.id,
                DealLedgerEntry.entry_type.in_(["issue", "risk"]),
                DealLedgerEntry.severity == "critical",
                DealLedgerEntry.status.in_(["open", "validated"]),
            )
        )
    )
    assembly = packet.scenario_snapshot.get("_assembly", {})
    assembly_mode = assembly.get("mode")
    governed_cases = packet.scenario_snapshot.get("cases", []) if assembly_mode == "governed" else []
    unapproved_case_ids = [
        item.get("id", "")
        for item in governed_cases
        if (item.get("latest_decision") or {}).get("decision") != "approved"
    ]
    sources_current, stale_source_ids = _governed_sources_current(
        session, deal, packet, actor
    )
    checks = [
        _check(
            "content_hash",
            _packet_hash_resolves(packet),
            "Packet content still matches its creation hash",
            [packet.id],
        ),
        _check(
            "assembly_contract",
            assembly_mode == "governed",
            "Packet was assembled by the server from governed model-of-record IDs",
            [packet.id],
        ),
        _check(
            "approved_case_versions",
            assembly_mode != "governed" or not unapproved_case_ids,
            "Every governed underwriting case version has an approved decision",
            unapproved_case_ids,
        ),
        _check(
            "governed_sources_current",
            sources_current,
            "Governed case approvals and evidence still resolve to their frozen versions",
            stale_source_ids,
        ),
        _check("deal_stage", deal.stage == "ic_review", "Deal is in IC review", [deal.id]),
        _check("team_lead", bool(lead_ids), "Deal lead or investment partner is assigned", lead_ids),
        _check(
            "required_gates",
            not unresolved_gates,
            "All required pre-IC and IC gates are satisfied or waived",
            [gate.id for gate in unresolved_gates],
        ),
        _check(
            "blocking_requests",
            not blocking_requests,
            "High-priority diligence requests are accepted or closed",
            [item.id for item in blocking_requests],
        ),
        _check(
            "critical_issues",
            not critical_issues,
            "Critical issues are mitigated, resolved, accepted, or rejected",
            [item.id for item in critical_issues],
        ),
        _check(
            "scenario_snapshot",
            bool(packet.scenario_snapshot and packet.model_snapshot),
            "Scenario and model snapshots are present",
            [packet.id] if packet.scenario_snapshot and packet.model_snapshot else [],
        ),
        _check(
            "evidence_manifest",
            bool(packet.evidence_manifest),
            "Evidence manifest is present",
            [packet.id] if packet.evidence_manifest else [],
        ),
        _check(
            "thesis_snapshot",
            bool(packet.thesis_snapshot),
            "Investment thesis is present",
            [packet.id] if packet.thesis_snapshot else [],
        ),
        _check(
            "decision_request",
            bool(packet.decision_request),
            "Requested IC decision is explicit",
            [packet.id] if packet.decision_request else [],
        ),
    ]
    result = {
        "packet_id": packet.id,
        "checked_at": now_utc(),
        "ready": all(check["passed"] for check in checks),
        "checks": checks,
    }
    packet.readiness_snapshot = _json_safe(result)
    packet.ready_for_submission = result["ready"]
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_packet.readiness_checked",
        packet,
        {"ready": result["ready"], "failed": [c["code"] for c in checks if not c["passed"]]},
    )
    _commit(session, packet)
    return result


def _check(code: str, passed: bool, message: str, entity_ids: Iterable[str]) -> dict:
    ids = list(entity_ids)
    return {
        "code": code,
        "passed": passed,
        "message": message,
        "blocking_count": 0 if passed else len(ids) or 1,
        "entity_ids": ids,
    }


def submit_ic_packet(
    session: Session, packet_id: str, actor: ActorContext | None = None
) -> ICPacket:
    packet = get_ic_packet(session, packet_id, actor)
    _require_governed_packet(packet, "submit")
    submitter = _require_actor(actor, "IC packet submission")
    if packet.status != "draft":
        raise WorkflowConflict(f"Only draft IC packets can be submitted, not '{packet.status}'")
    readiness = evaluate_ic_readiness(session, packet.id, actor)
    if not readiness["ready"]:
        failed = ", ".join(c["code"] for c in readiness["checks"] if not c["passed"])
        raise WorkflowConflict(f"IC packet is not ready; failed checks: {failed}")
    deal = _deal(session, packet.deal_id, actor)
    packet.status = "submitted"
    packet.submitted_by_actor_id = submitter
    packet.submitted_at = now_utc()
    packet.frozen_at = packet.submitted_at
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_packet.submitted",
        packet,
        {"version": packet.version, "content_hash": packet.content_hash},
    )
    return _commit(session, packet)


def add_ic_comment(
    session: Session,
    packet_id: str,
    data: ICCommentCreate,
    actor: ActorContext | None = None,
) -> ICComment:
    packet = get_ic_packet(session, packet_id, actor)
    deal = _deal(session, packet.deal_id, actor)
    author = _require_actor(actor, "IC comment")
    if packet.status == "superseded":
        raise WorkflowConflict("Cannot comment on a superseded packet")
    if data.parent_comment_id:
        parent = session.get(ICComment, data.parent_comment_id)
        if parent is None or parent.packet_id != packet.id:
            raise WorkflowError("parent_comment_id must reference the same IC packet")
    comment = ICComment(packet_id=packet.id, author_actor_id=author, **data.model_dump())
    session.add(comment)
    if packet.status == "submitted":
        packet.status = "in_review"
    session.flush()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_comment.created",
        comment,
        {"blocking": comment.blocking, "section_path": comment.section_path},
    )
    return _commit(session, comment)


def list_ic_comments(
    session: Session, packet_id: str, actor: ActorContext | None = None
) -> list[ICComment]:
    get_ic_packet(session, packet_id, actor)
    return list(
        session.scalars(
            select(ICComment)
            .where(ICComment.packet_id == packet_id)
            .order_by(ICComment.created_at)
        )
    )


def resolve_ic_comment(
    session: Session,
    comment_id: str,
    data: ICCommentResolve,
    actor: ActorContext | None = None,
) -> ICComment:
    comment = session.get(ICComment, comment_id)
    if comment is None:
        raise NotFound(f"IC comment '{comment_id}' not found")
    packet = get_ic_packet(session, comment.packet_id, actor)
    deal = _deal(session, packet.deal_id, actor)
    if comment.status == "resolved":
        raise WorkflowConflict("IC comment is already resolved")
    resolver = _require_actor(actor, "IC comment resolution")
    if comment.blocking and (
        not comment.author_actor_id or resolver == comment.author_actor_id
    ):
        raise WorkflowConflict("A blocking comment requires resolution by a second actor")
    comment.status = "resolved"
    comment.resolution = data.resolution
    comment.resolved_by_actor_id = resolver
    comment.resolved_at = now_utc()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_comment.resolved",
        comment,
        {"resolution": data.resolution},
    )
    return _commit(session, comment)


def record_ic_decision(
    session: Session,
    packet_id: str,
    data: ICDecisionCreate,
    actor: ActorContext | None = None,
) -> tuple[ICDecision, list[ConditionToClose]]:
    packet = get_ic_packet(session, packet_id, actor)
    _require_governed_packet(packet, "decide on")
    deal = _deal(session, packet.deal_id, actor)
    if packet.status not in {"submitted", "in_review", "deferred"}:
        raise WorkflowConflict(f"Packet in '{packet.status}' state cannot receive an IC decision")
    decider = _require_actor(actor, "IC decision")
    if not packet.submitted_by_actor_id:
        raise WorkflowConflict("The packet lacks an authenticated submitter")
    if decider == packet.submitted_by_actor_id:
        raise WorkflowConflict("The IC packet submitter cannot record the IC decision")
    if not _packet_hash_resolves(packet):
        raise WorkflowConflict("The frozen IC packet content hash no longer resolves")
    if data.decision in {"approve", "conditional"}:
        blocking_count = session.scalar(
            select(func.count())
            .select_from(ICComment)
            .where(
                ICComment.packet_id == packet.id,
                ICComment.blocking.is_(True),
                ICComment.status == "open",
            )
        ) or 0
        if blocking_count:
            raise WorkflowConflict(
                f"Resolve {blocking_count} blocking IC comment(s) before approval"
            )
    prior_final = session.scalar(
        select(ICDecision.id).where(ICDecision.packet_id == packet.id, ICDecision.is_final.is_(True))
    )
    if prior_final:
        raise WorkflowConflict("This IC packet already has a final decision")
    sequence = (
        session.scalar(
            select(func.max(ICDecision.sequence)).where(ICDecision.packet_id == packet.id)
        )
        or 0
    ) + 1
    decision = ICDecision(
        packet_id=packet.id,
        sequence=sequence,
        decision=data.decision,
        rationale=data.rationale,
        decided_by_actor_id=decider,
        meeting_at=data.meeting_at,
        is_final=data.decision != "defer",
    )
    session.add(decision)
    session.flush()
    conditions: list[ConditionToClose] = []
    for item in data.conditions:
        condition = ConditionToClose(
            deal_id=deal.id, packet_id=packet.id, decision_id=decision.id, **item.model_dump()
        )
        session.add(condition)
        conditions.append(condition)
    packet.status = {
        "approve": "approved",
        "conditional": "conditional",
        "defer": "deferred",
        "decline": "declined",
    }[data.decision]
    if data.decision == "decline":
        deal.status = "declined"
        deal.stage = "declined"
        deal.version += 1
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_decision.recorded",
        decision,
        {"decision": data.decision, "condition_count": len(conditions)},
    )
    _commit(session, decision)
    for condition in conditions:
        session.refresh(condition)
    return decision, conditions


def update_condition(
    session: Session,
    condition_id: str,
    data: ConditionPatch,
    actor: ActorContext | None = None,
) -> ConditionToClose:
    condition = session.get(ConditionToClose, condition_id)
    if condition is None:
        raise NotFound(f"Condition '{condition_id}' not found")
    deal = _deal(session, condition.deal_id, actor)
    if condition.status != "open":
        raise WorkflowConflict(f"Condition is already {condition.status}")
    resolver = _require_actor(actor, "Condition resolution")
    condition.status = data.status
    condition.resolution_note = data.resolution_note
    condition.evidence_refs = data.evidence_refs
    condition.resolved_by_actor_id = resolver
    condition.resolved_at = now_utc()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "condition.resolved",
        condition,
        {"status": condition.status, "evidence_refs": condition.evidence_refs},
    )
    return _commit(session, condition)


def list_conditions(
    session: Session, deal_id: str, actor: ActorContext | None = None
) -> list[ConditionToClose]:
    _deal(session, deal_id, actor)
    return list(
        session.scalars(
            select(ConditionToClose)
            .where(ConditionToClose.deal_id == deal_id)
            .order_by(ConditionToClose.due_date, ConditionToClose.created_at)
        )
    )


def diff_ic_packets(
    session: Session,
    from_packet_id: str,
    to_packet_id: str,
    actor: ActorContext | None = None,
) -> dict:
    before = get_ic_packet(session, from_packet_id, actor)
    after = get_ic_packet(session, to_packet_id, actor)
    if before.deal_id != after.deal_id:
        raise WorkflowError("IC packet comparisons must be within the same deal")
    before_data = _snapshot_dict(before)
    after_data = _snapshot_dict(after)
    return {
        "from_packet_id": before.id,
        "to_packet_id": after.id,
        "from_version": before.version,
        "to_version": after.version,
        "changes": _deep_diff(before_data, after_data),
    }


def _snapshot_dict(packet: ICPacket) -> dict:
    return {
        "title": packet.title,
        "scenario_snapshot": packet.scenario_snapshot,
        "model_snapshot": packet.model_snapshot,
        "evidence_manifest": packet.evidence_manifest,
        "thesis_snapshot": packet.thesis_snapshot,
        "risk_snapshot": packet.risk_snapshot,
        "decision_request": packet.decision_request,
    }


def _deep_diff(before: Any, after: Any, path: str = "$") -> list[dict]:
    if type(before) is not type(after):
        return [{"path": path, "change": "changed", "before": before, "after": after}]
    if isinstance(before, dict):
        changes: list[dict] = []
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}"
            if key not in before:
                changes.append(
                    {"path": child_path, "change": "added", "before": None, "after": after[key]}
                )
            elif key not in after:
                changes.append(
                    {"path": child_path, "change": "removed", "before": before[key], "after": None}
                )
            else:
                changes.extend(_deep_diff(before[key], after[key], child_path))
        return changes
    if isinstance(before, list):
        changes = []
        for index in range(max(len(before), len(after))):
            child_path = f"{path}[{index}]"
            if index >= len(before):
                changes.append(
                    {"path": child_path, "change": "added", "before": None, "after": after[index]}
                )
            elif index >= len(after):
                changes.append(
                    {"path": child_path, "change": "removed", "before": before[index], "after": None}
                )
            else:
                changes.extend(_deep_diff(before[index], after[index], child_path))
        return changes
    if before != after:
        return [{"path": path, "change": "changed", "before": before, "after": after}]
    return []


def create_export_manifest(
    session: Session,
    packet_id: str,
    data: ExportRequest,
    actor: ActorContext | None = None,
) -> ICPacketExport:
    packet = get_ic_packet(session, packet_id, actor)
    _require_governed_packet(packet, "export")
    deal = _deal(session, packet.deal_id, actor)
    if packet.status == "draft":
        raise WorkflowConflict("Freeze and submit an IC packet before exporting it")
    decisions = list(
        session.scalars(
            select(ICDecision).where(ICDecision.packet_id == packet.id).order_by(ICDecision.sequence)
        )
    )
    comments = list(
        session.scalars(select(ICComment).where(ICComment.packet_id == packet.id))
    )
    conditions = list(
        session.scalars(select(ConditionToClose).where(ConditionToClose.packet_id == packet.id))
    )
    sections = []
    for name, value in _snapshot_dict(packet).items():
        sections.append(
            {
                "name": name,
                "sha256": _sha256(value),
                "item_count": len(value) if hasattr(value, "__len__") else 1,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "format": data.format,
        "generated_at": now_utc(),
        "organization_id": deal.organization_id,
        "fund_id": deal.fund_id,
        "deal": {"id": deal.id, "code": deal.code, "name": deal.name, "stage": deal.stage},
        "packet": {
            "id": packet.id,
            "version": packet.version,
            "status": packet.status,
            "content_hash": packet.content_hash,
            "frozen_at": packet.frozen_at,
        },
        "sections": sections,
        "evidence_manifest": packet.evidence_manifest,
        "decisions": [
            {
                "id": item.id,
                "sequence": item.sequence,
                "decision": item.decision,
                "decided_by_actor_id": item.decided_by_actor_id,
                "decided_at": item.decided_at,
            }
            for item in decisions
        ],
        "review": {
            "comment_count": len(comments),
            "open_blocking_comment_ids": [
                item.id for item in comments if item.blocking and item.status == "open"
            ],
        },
        "conditions": [
            {"id": item.id, "status": item.status, "description": item.description}
            for item in conditions
        ],
    }
    safe_manifest = _json_safe(manifest)
    export = ICPacketExport(
        packet_id=packet.id,
        format=data.format,
        manifest=safe_manifest,
        manifest_hash=_sha256(safe_manifest),
        requested_by_actor_id=_actor_id(actor),
    )
    session.add(export)
    session.flush()
    _audit(
        session,
        deal.organization_id,
        deal.id,
        actor,
        "ic_packet.export_manifest_created",
        export,
        {"format": data.format, "manifest_hash": export.manifest_hash},
    )
    return _commit(session, export)


def verify_export_manifest(
    session: Session,
    export_id: str,
    actor: ActorContext | None = None,
) -> dict[str, Any]:
    """Recompute an export's canonical hash and resolve every governed source binding."""
    export = session.get(ICPacketExport, export_id)
    if export is None:
        raise NotFound(f"IC export '{export_id}' not found")
    packet = session.get(ICPacket, export.packet_id)
    if packet is None:
        raise WorkflowConflict("The export's packet no longer resolves")
    deal = _deal(session, packet.deal_id, actor)
    manifest = export.manifest or {}
    recomputed_manifest_hash = _sha256(manifest)
    checks: list[dict[str, Any]] = []

    def add_check(
        code: str,
        passed: bool,
        message: str,
        entity_ids: list[str] | None = None,
    ) -> None:
        ids = list(dict.fromkeys(entity_ids or []))
        checks.append(
            {
                "code": code,
                "passed": passed,
                "message": message,
                "blocking_count": 0 if passed else max(1, len(ids)),
                "entity_ids": ids,
            }
        )

    manifest_hash_ok = recomputed_manifest_hash == export.manifest_hash
    add_check(
        "canonical_manifest_hash",
        manifest_hash_ok,
        "Canonical manifest hash resolves"
        if manifest_hash_ok
        else "Canonical manifest hash does not match the stored digest",
        [] if manifest_hash_ok else [export.id],
    )

    packet_data = manifest.get("packet") if isinstance(manifest.get("packet"), dict) else {}
    bound_packet_id = packet_data.get("id") or manifest.get("packet_id")
    bound_packet_version = packet_data.get("version") or manifest.get("packet_version")
    packet_binding_ok = (
        export.packet_id == packet.id
        and bound_packet_id == packet.id
        and bound_packet_version == packet.version
    )
    add_check(
        "packet_binding",
        packet_binding_ok,
        "Manifest is bound to the persisted packet id and version"
        if packet_binding_ok
        else "Manifest packet id or version does not resolve",
        [] if packet_binding_ok else [packet.id],
    )

    bound_content_hash = packet_data.get("content_hash") or manifest.get(
        "packet_content_hash"
    )
    packet_content_ok = (
        bound_content_hash == packet.content_hash and _packet_hash_resolves(packet)
    )
    add_check(
        "packet_content_binding",
        packet_content_ok,
        "Manifest and canonical packet payload resolve to the frozen content hash"
        if packet_content_ok
        else "Packet content hash or manifest content binding does not resolve",
        [] if packet_content_ok else [packet.id],
    )

    sections = manifest.get("sections")
    snapshot = _snapshot_dict(packet)
    if isinstance(sections, list) and all(isinstance(item, dict) for item in sections):
        expected_sections = [
            {
                "name": name,
                "sha256": _sha256(value),
                "item_count": len(value) if hasattr(value, "__len__") else 1,
            }
            for name, value in snapshot.items()
        ]
        sections_ok = sections == expected_sections
    elif isinstance(sections, list) and all(isinstance(item, str) for item in sections):
        # File-export manifests bind the complete packet through packet_content_hash and name
        # every rendered section; the exported bytes themselves are verified by file_sha256.
        required = {
            "decision_request",
            "scenario_snapshot",
            "model_snapshot",
            "thesis_snapshot",
            "risk_snapshot",
            "evidence_manifest",
        }
        sections_ok = required.issubset(set(sections))
    else:
        sections_ok = False
    add_check(
        "section_bindings",
        sections_ok,
        "Every exported packet section is present and canonically bound"
        if sections_ok
        else "One or more export section bindings do not resolve",
        [] if sections_ok else [export.id],
    )

    manifest_evidence = manifest.get("evidence_manifest")
    manifest_evidence_ok = (
        manifest_evidence == packet.evidence_manifest
        if manifest_evidence is not None
        else sections_ok
    )
    case_ok, stale_cases, evidence_ok, stale_evidence = _governed_packet_bindings_resolve(
        session, deal, packet
    )
    evidence_binding_ok = manifest_evidence_ok and evidence_ok
    add_check(
        "evidence_bindings",
        evidence_binding_ok,
        "Evidence entries resolve to their exact governed claim, review, document, chunk, and Evidence rows"
        if evidence_binding_ok
        else "One or more governed evidence bindings do not resolve",
        [] if evidence_binding_ok else stale_evidence or [packet.id],
    )
    add_check(
        "case_bindings",
        case_ok,
        "Underwriting case inputs, outputs, and approval snapshots resolve"
        if case_ok
        else "One or more underwriting case bindings do not resolve",
        [] if case_ok else stale_cases or [packet.id],
    )

    return {
        "export_id": export.id,
        "packet_id": packet.id,
        "verified_at": now_utc(),
        "valid": all(item["passed"] for item in checks),
        "manifest_hash": export.manifest_hash,
        "recomputed_manifest_hash": recomputed_manifest_hash,
        "checks": checks,
    }


def list_audit_events(
    session: Session,
    deal_id: str,
    actor: ActorContext | None = None,
    *,
    limit: int = 200,
) -> list[WorkflowAuditEvent]:
    _deal(session, deal_id, actor)
    return list(
        session.scalars(
            select(WorkflowAuditEvent)
            .where(WorkflowAuditEvent.deal_id == deal_id)
            .order_by(WorkflowAuditEvent.created_at.desc())
            .limit(min(max(limit, 1), 1_000))
        )
    )
