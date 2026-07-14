"""HTTP surface for institutional deal workflow and investment-committee governance."""
from __future__ import annotations

import io
from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.routers.deps import SessionDep
from src.schemas.deal_workflow import (
    ActorContext,
    ConditionOut,
    ConditionPatch,
    DealCreate,
    DealOut,
    DealPatch,
    DiligenceAttachmentCreate,
    DiligenceAttachmentOut,
    DiligenceRequestCreate,
    DiligenceRequestOut,
    DiligenceResponseCreate,
    DiligenceResponseOut,
    DiligenceReview,
    ExportManifestOut,
    ExportRequest,
    ExportVerificationResult,
    FundCreate,
    FundOut,
    ICCommentCreate,
    ICCommentOut,
    ICCommentResolve,
    ICDecisionCreate,
    ICDecisionOut,
    ICDecisionResult,
    ICPacketCreate,
    ICPacketOut,
    LedgerEntryCreate,
    LedgerEntryOut,
    LedgerEntryRevision,
    MilestoneCreate,
    MilestoneOut,
    MilestonePatch,
    OrganizationCreate,
    OrganizationOut,
    PacketDiffResult,
    ReadinessResult,
    StageGateCreate,
    StageGateOut,
    StageGateResolve,
    StageTransitionCreate,
    StageTransitionOut,
    TaskCreate,
    TaskOut,
    TaskPatch,
    TeamMemberCreate,
    TeamMemberOut,
    WorkflowAuditOut,
    WorkstreamCreate,
    WorkstreamOut,
    WorkstreamPatch,
)
from src.services import deal_workflow_service as service
from src.services import ic_export_service

router = APIRouter(prefix="/api", tags=["deal workflow"])
T = TypeVar("T")


def actor_context(
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    header_display_name: Annotated[str | None, Header(alias="X-Actor-Name")] = None,
    header_organization_id: Annotated[str | None, Header(alias="X-Organization-ID")] = None,
    header_roles: Annotated[str | None, Header(alias="X-Actor-Roles")] = None,
    header_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> ActorContext:
    """Use the server-verified principal; headers are an auth-off development fallback only."""
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return ActorContext(
            actor_id=principal.user_id,
            display_name=principal.display_name,
            organization_id=principal.organization_id,
            roles=principal.actor_roles,
            request_id=header_request_id,
        )
    return ActorContext(
        actor_id=header_actor_id,
        display_name=header_display_name,
        organization_id=header_organization_id,
        roles=tuple(item.strip() for item in (header_roles or "").split(",") if item.strip()),
        request_id=header_request_id,
    )


ActorDep = Annotated[ActorContext, Depends(actor_context)]


def _call(function: Callable[..., T], *args, **kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except service.WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/organizations", response_model=OrganizationOut, status_code=201)
def create_organization(
    payload: OrganizationCreate,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> OrganizationOut:
    if getattr(request.state, "principal", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="Authenticated organization provisioning must use the registration flow",
        )
    return OrganizationOut.model_validate(_call(service.create_organization, session, payload, actor))


@router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(session: SessionDep, actor: ActorDep) -> list[OrganizationOut]:
    return [
        OrganizationOut.model_validate(item)
        for item in _call(service.list_organizations, session, actor)
    ]


@router.post("/organizations/{organization_id}/funds", response_model=FundOut, status_code=201)
def create_fund(
    organization_id: str, payload: FundCreate, session: SessionDep, actor: ActorDep
) -> FundOut:
    return FundOut.model_validate(
        _call(service.create_fund, session, organization_id, payload, actor)
    )


@router.get("/organizations/{organization_id}/funds", response_model=list[FundOut])
def list_funds(organization_id: str, session: SessionDep, actor: ActorDep) -> list[FundOut]:
    return [
        FundOut.model_validate(item)
        for item in _call(service.list_funds, session, organization_id, actor)
    ]


@router.post("/funds/{fund_id}/deals", response_model=DealOut, status_code=201)
def create_deal(
    fund_id: str, payload: DealCreate, session: SessionDep, actor: ActorDep
) -> DealOut:
    return DealOut.model_validate(_call(service.create_deal, session, fund_id, payload, actor))


@router.get("/organizations/{organization_id}/deals", response_model=list[DealOut])
def list_deals(
    organization_id: str,
    session: SessionDep,
    actor: ActorDep,
    stage: str | None = None,
    fund_id: str | None = None,
) -> list[DealOut]:
    return [
        DealOut.model_validate(item)
        for item in _call(
            service.list_deals,
            session,
            organization_id,
            actor,
            stage=stage,
            fund_id=fund_id,
        )
    ]


@router.get("/deals/{deal_id}", response_model=DealOut)
def get_deal(deal_id: str, session: SessionDep, actor: ActorDep) -> DealOut:
    return DealOut.model_validate(_call(service.get_deal, session, deal_id, actor))


@router.get("/workspaces/{workspace_id}/deal", response_model=DealOut)
def get_workspace_deal(
    workspace_id: str, session: SessionDep, actor: ActorDep
) -> DealOut:
    return DealOut.model_validate(
        _call(service.get_deal_by_workspace, session, workspace_id, actor)
    )


@router.patch("/deals/{deal_id}", response_model=DealOut)
def update_deal(
    deal_id: str, payload: DealPatch, session: SessionDep, actor: ActorDep
) -> DealOut:
    return DealOut.model_validate(_call(service.update_deal, session, deal_id, payload, actor))


@router.get("/deals/{deal_id}/gates", response_model=list[StageGateOut])
def list_gates(
    deal_id: str, session: SessionDep, actor: ActorDep, stage: str | None = None
) -> list[StageGateOut]:
    return [
        StageGateOut.model_validate(item)
        for item in _call(service.list_gates, session, deal_id, actor, stage)
    ]


@router.post("/deals/{deal_id}/gates", response_model=StageGateOut, status_code=201)
def create_gate(
    deal_id: str, payload: StageGateCreate, session: SessionDep, actor: ActorDep
) -> StageGateOut:
    return StageGateOut.model_validate(
        _call(service.create_gate, session, deal_id, payload, actor)
    )


@router.post("/stage-gates/{gate_id}/resolve", response_model=StageGateOut)
def resolve_gate(
    gate_id: str, payload: StageGateResolve, session: SessionDep, actor: ActorDep
) -> StageGateOut:
    return StageGateOut.model_validate(
        _call(service.resolve_gate, session, gate_id, payload, actor)
    )


@router.post("/deals/{deal_id}/stage-transitions", response_model=StageTransitionOut)
def transition_deal(
    deal_id: str, payload: StageTransitionCreate, session: SessionDep, actor: ActorDep
) -> StageTransitionOut:
    return StageTransitionOut.model_validate(
        _call(service.transition_deal, session, deal_id, payload, actor)
    )


@router.post("/deals/{deal_id}/team", response_model=TeamMemberOut, status_code=201)
def add_team_member(
    deal_id: str, payload: TeamMemberCreate, session: SessionDep, actor: ActorDep
) -> TeamMemberOut:
    return TeamMemberOut.model_validate(
        _call(service.add_team_member, session, deal_id, payload, actor)
    )


@router.get("/deals/{deal_id}/team", response_model=list[TeamMemberOut])
def list_team(deal_id: str, session: SessionDep, actor: ActorDep) -> list[TeamMemberOut]:
    return [
        TeamMemberOut.model_validate(item)
        for item in _call(service.list_team, session, deal_id, actor)
    ]


@router.post("/deals/{deal_id}/workstreams", response_model=WorkstreamOut, status_code=201)
def create_workstream(
    deal_id: str, payload: WorkstreamCreate, session: SessionDep, actor: ActorDep
) -> WorkstreamOut:
    return WorkstreamOut.model_validate(
        _call(service.create_workstream, session, deal_id, payload, actor)
    )


@router.get("/deals/{deal_id}/workstreams", response_model=list[WorkstreamOut])
def list_workstreams(
    deal_id: str, session: SessionDep, actor: ActorDep
) -> list[WorkstreamOut]:
    return [
        WorkstreamOut.model_validate(item)
        for item in _call(service.list_workstreams, session, deal_id, actor)
    ]


@router.patch("/workstreams/{workstream_id}", response_model=WorkstreamOut)
def update_workstream(
    workstream_id: str, payload: WorkstreamPatch, session: SessionDep, actor: ActorDep
) -> WorkstreamOut:
    return WorkstreamOut.model_validate(
        _call(service.update_workstream, session, workstream_id, payload, actor)
    )


@router.post("/deals/{deal_id}/milestones", response_model=MilestoneOut, status_code=201)
def create_milestone(
    deal_id: str, payload: MilestoneCreate, session: SessionDep, actor: ActorDep
) -> MilestoneOut:
    return MilestoneOut.model_validate(
        _call(service.create_milestone, session, deal_id, payload, actor)
    )


@router.get("/deals/{deal_id}/milestones", response_model=list[MilestoneOut])
def list_milestones(
    deal_id: str, session: SessionDep, actor: ActorDep
) -> list[MilestoneOut]:
    return [
        MilestoneOut.model_validate(item)
        for item in _call(service.list_milestones, session, deal_id, actor)
    ]


@router.patch("/milestones/{milestone_id}", response_model=MilestoneOut)
def update_milestone(
    milestone_id: str, payload: MilestonePatch, session: SessionDep, actor: ActorDep
) -> MilestoneOut:
    return MilestoneOut.model_validate(
        _call(service.update_milestone, session, milestone_id, payload, actor)
    )


@router.post("/deals/{deal_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(
    deal_id: str, payload: TaskCreate, session: SessionDep, actor: ActorDep
) -> TaskOut:
    return TaskOut.model_validate(_call(service.create_task, session, deal_id, payload, actor))


@router.get("/deals/{deal_id}/tasks", response_model=list[TaskOut])
def list_tasks(
    deal_id: str, session: SessionDep, actor: ActorDep, status: str | None = None
) -> list[TaskOut]:
    return [
        TaskOut.model_validate(item)
        for item in _call(service.list_tasks, session, deal_id, actor, status=status)
    ]


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: str, payload: TaskPatch, session: SessionDep, actor: ActorDep
) -> TaskOut:
    return TaskOut.model_validate(_call(service.update_task, session, task_id, payload, actor))


@router.post(
    "/deals/{deal_id}/diligence-requests", response_model=DiligenceRequestOut, status_code=201
)
def create_diligence_request(
    deal_id: str, payload: DiligenceRequestCreate, session: SessionDep, actor: ActorDep
) -> DiligenceRequestOut:
    return DiligenceRequestOut.model_validate(
        _call(service.create_diligence_request, session, deal_id, payload, actor)
    )


@router.get("/deals/{deal_id}/diligence-requests", response_model=list[DiligenceRequestOut])
def list_diligence_requests(
    deal_id: str, session: SessionDep, actor: ActorDep, status: str | None = None
) -> list[DiligenceRequestOut]:
    return [
        DiligenceRequestOut.model_validate(item)
        for item in _call(
            service.list_diligence_requests, session, deal_id, actor, status=status
        )
    ]


@router.post("/diligence-requests/{request_id}/send", response_model=DiligenceRequestOut)
def send_diligence_request(
    request_id: str, session: SessionDep, actor: ActorDep
) -> DiligenceRequestOut:
    return DiligenceRequestOut.model_validate(
        _call(service.send_diligence_request, session, request_id, actor)
    )


@router.post(
    "/diligence-requests/{request_id}/responses",
    response_model=DiligenceResponseOut,
    status_code=201,
)
def add_diligence_response(
    request_id: str, payload: DiligenceResponseCreate, session: SessionDep, actor: ActorDep
) -> DiligenceResponseOut:
    return DiligenceResponseOut.model_validate(
        _call(service.add_diligence_response, session, request_id, payload, actor)
    )


@router.post(
    "/diligence-requests/{request_id}/attachments",
    response_model=DiligenceAttachmentOut,
    status_code=201,
)
def add_diligence_attachment(
    request_id: str,
    payload: DiligenceAttachmentCreate,
    session: SessionDep,
    actor: ActorDep,
) -> DiligenceAttachmentOut:
    return DiligenceAttachmentOut.model_validate(
        _call(service.add_diligence_attachment, session, request_id, payload, actor)
    )


@router.post("/diligence-requests/{request_id}/review", response_model=DiligenceRequestOut)
def review_diligence_request(
    request_id: str, payload: DiligenceReview, session: SessionDep, actor: ActorDep
) -> DiligenceRequestOut:
    return DiligenceRequestOut.model_validate(
        _call(service.review_diligence_request, session, request_id, payload, actor)
    )


@router.post("/deals/{deal_id}/ledger", response_model=LedgerEntryOut, status_code=201)
def create_ledger_entry(
    deal_id: str, payload: LedgerEntryCreate, session: SessionDep, actor: ActorDep
) -> LedgerEntryOut:
    return LedgerEntryOut.model_validate(
        _call(service.create_ledger_entry, session, deal_id, payload, actor)
    )


@router.get("/deals/{deal_id}/ledger", response_model=list[LedgerEntryOut])
def list_ledger_entries(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    include_superseded: bool = False,
    entry_type: str | None = None,
) -> list[LedgerEntryOut]:
    return [
        LedgerEntryOut.model_validate(item)
        for item in _call(
            service.list_ledger_entries,
            session,
            deal_id,
            actor,
            include_superseded=include_superseded,
            entry_type=entry_type,
        )
    ]


@router.post("/ledger/{entry_id}/revisions", response_model=LedgerEntryOut, status_code=201)
def revise_ledger_entry(
    entry_id: str, payload: LedgerEntryRevision, session: SessionDep, actor: ActorDep
) -> LedgerEntryOut:
    return LedgerEntryOut.model_validate(
        _call(service.revise_ledger_entry, session, entry_id, payload, actor)
    )


@router.post("/deals/{deal_id}/ic-packets", response_model=ICPacketOut, status_code=201)
def create_ic_packet(
    deal_id: str, payload: ICPacketCreate, session: SessionDep, actor: ActorDep
) -> ICPacketOut:
    return ICPacketOut.model_validate(
        _call(service.create_ic_packet, session, deal_id, payload, actor)
    )


@router.get("/deals/{deal_id}/ic-packets", response_model=list[ICPacketOut])
def list_ic_packets(
    deal_id: str, session: SessionDep, actor: ActorDep
) -> list[ICPacketOut]:
    return [
        ICPacketOut.model_validate(item)
        for item in _call(service.list_ic_packets, session, deal_id, actor)
    ]


@router.get("/ic-packets/{packet_id}", response_model=ICPacketOut)
def get_ic_packet(packet_id: str, session: SessionDep, actor: ActorDep) -> ICPacketOut:
    return ICPacketOut.model_validate(_call(service.get_ic_packet, session, packet_id, actor))


@router.post("/ic-packets/{packet_id}/readiness", response_model=ReadinessResult)
def check_ic_readiness(
    packet_id: str, session: SessionDep, actor: ActorDep
) -> ReadinessResult:
    return ReadinessResult.model_validate(
        _call(service.evaluate_ic_readiness, session, packet_id, actor)
    )


@router.post("/ic-packets/{packet_id}/submit", response_model=ICPacketOut)
def submit_ic_packet(packet_id: str, session: SessionDep, actor: ActorDep) -> ICPacketOut:
    return ICPacketOut.model_validate(
        _call(service.submit_ic_packet, session, packet_id, actor)
    )


@router.post("/ic-packets/{packet_id}/comments", response_model=ICCommentOut, status_code=201)
def add_ic_comment(
    packet_id: str, payload: ICCommentCreate, session: SessionDep, actor: ActorDep
) -> ICCommentOut:
    return ICCommentOut.model_validate(
        _call(service.add_ic_comment, session, packet_id, payload, actor)
    )


@router.get("/ic-packets/{packet_id}/comments", response_model=list[ICCommentOut])
def list_ic_comments(
    packet_id: str, session: SessionDep, actor: ActorDep
) -> list[ICCommentOut]:
    return [
        ICCommentOut.model_validate(item)
        for item in _call(service.list_ic_comments, session, packet_id, actor)
    ]


@router.post("/ic-comments/{comment_id}/resolve", response_model=ICCommentOut)
def resolve_ic_comment(
    comment_id: str, payload: ICCommentResolve, session: SessionDep, actor: ActorDep
) -> ICCommentOut:
    return ICCommentOut.model_validate(
        _call(service.resolve_ic_comment, session, comment_id, payload, actor)
    )


@router.post("/ic-packets/{packet_id}/decisions", response_model=ICDecisionResult)
def record_ic_decision(
    packet_id: str, payload: ICDecisionCreate, session: SessionDep, actor: ActorDep
) -> ICDecisionResult:
    decision, conditions = _call(service.record_ic_decision, session, packet_id, payload, actor)
    return ICDecisionResult(
        decision=ICDecisionOut.model_validate(decision),
        conditions=[ConditionOut.model_validate(item) for item in conditions],
    )


@router.get("/deals/{deal_id}/conditions", response_model=list[ConditionOut])
def list_conditions(
    deal_id: str, session: SessionDep, actor: ActorDep
) -> list[ConditionOut]:
    return [
        ConditionOut.model_validate(item)
        for item in _call(service.list_conditions, session, deal_id, actor)
    ]


@router.patch("/conditions/{condition_id}", response_model=ConditionOut)
def update_condition(
    condition_id: str, payload: ConditionPatch, session: SessionDep, actor: ActorDep
) -> ConditionOut:
    return ConditionOut.model_validate(
        _call(service.update_condition, session, condition_id, payload, actor)
    )


@router.get(
    "/ic-packets/{from_packet_id}/diff/{to_packet_id}", response_model=PacketDiffResult
)
def diff_ic_packets(
    from_packet_id: str, to_packet_id: str, session: SessionDep, actor: ActorDep
) -> PacketDiffResult:
    return PacketDiffResult.model_validate(
        _call(service.diff_ic_packets, session, from_packet_id, to_packet_id, actor)
    )


@router.post(
    "/ic-packets/{packet_id}/export-manifests",
    response_model=ExportManifestOut,
    status_code=201,
)
def create_export_manifest(
    packet_id: str, payload: ExportRequest, session: SessionDep, actor: ActorDep
) -> ExportManifestOut:
    return ExportManifestOut.model_validate(
        _call(service.create_export_manifest, session, packet_id, payload, actor)
    )


@router.get(
    "/ic-exports/{export_id}/verification",
    response_model=ExportVerificationResult,
)
def verify_export_manifest(
    export_id: str, session: SessionDep, actor: ActorDep
) -> ExportVerificationResult:
    return ExportVerificationResult.model_validate(
        _call(service.verify_export_manifest, session, export_id, actor)
    )


@router.post("/ic-packets/{packet_id}/exports", response_class=StreamingResponse)
def export_ic_packet(
    packet_id: str, payload: ExportRequest, session: SessionDep, actor: ActorDep
) -> StreamingResponse:
    exported = _call(ic_export_service.render_and_record_export, session, packet_id, payload, actor)
    return StreamingResponse(
        io.BytesIO(exported.content),
        media_type=exported.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Export-ID": exported.export_id,
            "X-Content-SHA256": exported.sha256,
        },
    )


@router.get("/deals/{deal_id}/audit-events", response_model=list[WorkflowAuditOut])
def list_audit_events(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
) -> list[WorkflowAuditOut]:
    return [
        WorkflowAuditOut.model_validate(item)
        for item in _call(service.list_audit_events, session, deal_id, actor, limit=limit)
    ]
