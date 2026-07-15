"""Versioned underwriting, LBO, valuation, and stress-testing endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from src.routers.deps import OptionalPrincipalDep, SessionDep, require_scope
from src.schemas.deal_workflow import ActorContext
from src.schemas.underwriting_model import (
    CaseKey,
    CaseVarianceRequest,
    CaseVarianceResult,
    CovenantHeadroomResult,
    DriverModelRequest,
    DriverModelResult,
    ExitReadinessResult,
    FootballFieldResult,
    MonteCarloRequest,
    MonteCarloResult,
    RecapBoltOnRequest,
    RecapBoltOnResult,
    ReturnsAttributionRequest,
    ReturnsAttributionResult,
    ReverseStressRequest,
    ReverseStressResult,
    SensitivityRequest,
    SensitivityResult,
    UnderwritingAssumptions,
    UnderwritingCalculateRequest,
    UnderwritingCaseCreate,
    UnderwritingCaseSetCreate,
    UnderwritingCaseVersionOut,
    UnderwritingDecisionCreate,
    UnderwritingDecisionOut,
    UnderwritingResult,
    ValuationTriangulationRequest,
    ValuationTriangulationResult,
    WorkingCapitalPegRequest,
    WorkingCapitalPegResult,
    WorkingCapitalSeasonalityRequest,
    WorkingCapitalSeasonalityResult,
)
from src.services import underwriting_model_service as service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["underwriting"])


def _verified_actor(principal, header_actor_id: str | None, fallback: str) -> str:
    """A session principal always wins; actor headers are only a local auth-off fallback."""
    return principal.user_id if principal is not None else (header_actor_id or fallback)


def _actor_context(principal, header_actor_id: str | None, fallback: str) -> ActorContext:
    return ActorContext(
        actor_id=_verified_actor(principal, header_actor_id, fallback),
        display_name=principal.display_name if principal is not None else None,
        organization_id=principal.organization_id if principal is not None else None,
        roles=principal.actor_roles if principal is not None else (),
    )


def _calculation_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/{workspace_id}/underwriting/calculate", response_model=UnderwritingResult)
def calculate(
    workspace_id: str, payload: UnderwritingCalculateRequest, session: SessionDep
) -> UnderwritingResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.run_underwriting(payload.assumptions)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/cases",
    response_model=UnderwritingCaseVersionOut,
    status_code=201,
    dependencies=[Depends(require_scope("write:underwriting"))],
)
def create_case(
    workspace_id: str,
    payload: UnderwritingCaseCreate,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> UnderwritingCaseVersionOut:
    actor = _actor_context(principal, header_actor_id, payload.created_by)
    payload = payload.model_copy(
        update={"created_by": actor.actor_id}
    )
    try:
        record = service.create_case_version(session, workspace_id, payload, actor)
    except service.CaseVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.CaseEvidenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc
    return UnderwritingCaseVersionOut.model_validate(service.case_version_payload(session, record))


@router.post(
    "/{workspace_id}/underwriting/case-set",
    response_model=list[UnderwritingCaseVersionOut],
    status_code=201,
)
def create_case_set(
    workspace_id: str,
    payload: UnderwritingCaseSetCreate,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> list[UnderwritingCaseVersionOut]:
    actor = _actor_context(principal, header_actor_id, "system")
    payload = payload.model_copy(
        update={
            "cases": [
                case.model_copy(update={"created_by": actor.actor_id})
                for case in payload.cases
            ]
        }
    )
    try:
        records = service.create_case_set(session, workspace_id, payload.cases, actor)
    except service.CaseVersionConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.CaseEvidenceError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except service.UnderwritingCalculationError as exc:
        session.rollback()
        raise _calculation_error(exc) from exc
    return [
        UnderwritingCaseVersionOut.model_validate(service.case_version_payload(session, record))
        for record in records
    ]


@router.get(
    "/{workspace_id}/underwriting/cases",
    response_model=list[UnderwritingCaseVersionOut],
    dependencies=[Depends(require_scope("read:underwriting"))],
)
def list_latest_cases(workspace_id: str, session: SessionDep) -> list[UnderwritingCaseVersionOut]:
    records = service.list_case_versions(session, workspace_id, latest_only=True)
    return [
        UnderwritingCaseVersionOut.model_validate(service.case_version_payload(session, record))
        for record in records
    ]


@router.get(
    "/{workspace_id}/underwriting/cases/{case_key}/versions",
    response_model=list[UnderwritingCaseVersionOut],
)
def list_versions(
    workspace_id: str, case_key: CaseKey, session: SessionDep
) -> list[UnderwritingCaseVersionOut]:
    records = service.list_case_versions(session, workspace_id, case_key=case_key)
    return [
        UnderwritingCaseVersionOut.model_validate(service.case_version_payload(session, record))
        for record in records
    ]


@router.get(
    "/{workspace_id}/underwriting/cases/{case_key}/versions/{version}",
    response_model=UnderwritingCaseVersionOut,
)
def get_version(
    workspace_id: str, case_key: CaseKey, version: int, session: SessionDep
) -> UnderwritingCaseVersionOut:
    record = service.get_case_version(session, workspace_id, case_key, version)
    return UnderwritingCaseVersionOut.model_validate(service.case_version_payload(session, record))


@router.get(
    "/{workspace_id}/underwriting/cases/{case_key}",
    response_model=UnderwritingCaseVersionOut,
)
def get_latest_case(
    workspace_id: str, case_key: CaseKey, session: SessionDep
) -> UnderwritingCaseVersionOut:
    record = service.get_case_version(session, workspace_id, case_key)
    return UnderwritingCaseVersionOut.model_validate(service.case_version_payload(session, record))


@router.post(
    "/{workspace_id}/underwriting/cases/{case_key}/versions/{version}/decisions",
    response_model=UnderwritingDecisionOut,
    status_code=201,
)
def create_decision(
    workspace_id: str,
    case_key: CaseKey,
    version: int,
    payload: UnderwritingDecisionCreate,
    session: SessionDep,
    principal: OptionalPrincipalDep,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> UnderwritingDecisionOut:
    payload = payload.model_copy(
        update={"actor": _verified_actor(principal, header_actor_id, payload.actor)}
    )
    try:
        decision = service.add_case_decision(session, workspace_id, case_key, version, payload)
    except (service.CaseVersionConflict, ValueError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UnderwritingDecisionOut.model_validate(decision)


@router.post(
    "/{workspace_id}/underwriting/working-capital-peg",
    response_model=WorkingCapitalPegResult,
)
def working_capital_peg(
    workspace_id: str, payload: WorkingCapitalPegRequest, session: SessionDep
) -> WorkingCapitalPegResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_working_capital_peg(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/driver-model",
    response_model=DriverModelResult,
)
def driver_model(
    workspace_id: str, payload: DriverModelRequest, session: SessionDep
) -> DriverModelResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_driver_model(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/working-capital-seasonality",
    response_model=WorkingCapitalSeasonalityResult,
)
def working_capital_seasonality(
    workspace_id: str, payload: WorkingCapitalSeasonalityRequest, session: SessionDep
) -> WorkingCapitalSeasonalityResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_working_capital_seasonality(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/recap-boltons",
    response_model=RecapBoltOnResult,
)
def recap_boltons(
    workspace_id: str, payload: RecapBoltOnRequest, session: SessionDep
) -> RecapBoltOnResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_recap_boltons(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/sensitivity",
    response_model=SensitivityResult,
)
def sensitivity(
    workspace_id: str, payload: SensitivityRequest, session: SessionDep
) -> SensitivityResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_sensitivity(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/reverse-stress",
    response_model=ReverseStressResult,
)
def reverse_stress(
    workspace_id: str, payload: ReverseStressRequest, session: SessionDep
) -> ReverseStressResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_reverse_stress(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/monte-carlo",
    response_model=MonteCarloResult,
)
def monte_carlo(
    workspace_id: str, payload: MonteCarloRequest, session: SessionDep
) -> MonteCarloResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.run_monte_carlo(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/returns-attribution",
    response_model=ReturnsAttributionResult,
)
def returns_attribution(
    workspace_id: str, payload: ReturnsAttributionRequest, session: SessionDep
) -> ReturnsAttributionResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_returns_attribution(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/valuation-triangulation",
    response_model=ValuationTriangulationResult,
)
def valuation_triangulation(
    workspace_id: str, payload: ValuationTriangulationRequest, session: SessionDep
) -> ValuationTriangulationResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_valuation_triangulation(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/covenant-headroom",
    response_model=CovenantHeadroomResult,
)
def covenant_headroom(
    workspace_id: str, payload: UnderwritingCalculateRequest, session: SessionDep
) -> CovenantHeadroomResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_covenant_headroom(payload.assumptions)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


def _resolve_variance_operand(session, workspace_id: str, operand) -> tuple:
    if operand.assumptions is not None:
        return operand.assumptions, "custom"
    record = service.get_case_version(session, workspace_id, operand.case_key, operand.version)
    assumptions = UnderwritingAssumptions.model_validate(record.assumptions)
    suffix = "latest" if operand.version is None else f"v{operand.version}"
    return assumptions, f"{operand.case_key} ({suffix})"


@router.post(
    "/{workspace_id}/underwriting/case-variance",
    response_model=CaseVarianceResult,
)
def case_variance(
    workspace_id: str, payload: CaseVarianceRequest, session: SessionDep
) -> CaseVarianceResult:
    get_workspace_or_404(session, workspace_id)
    management, management_label = _resolve_variance_operand(
        session, workspace_id, payload.management
    )
    sponsor, sponsor_label = _resolve_variance_operand(session, workspace_id, payload.sponsor)
    try:
        return service.calculate_case_variance(
            management, sponsor, management_label, sponsor_label
        )
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/exit-readiness",
    response_model=ExitReadinessResult,
)
def exit_readiness(
    workspace_id: str, payload: UnderwritingCalculateRequest, session: SessionDep
) -> ExitReadinessResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_exit_readiness(payload.assumptions)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc


@router.post(
    "/{workspace_id}/underwriting/football-field",
    response_model=FootballFieldResult,
)
def football_field(
    workspace_id: str, payload: ValuationTriangulationRequest, session: SessionDep
) -> FootballFieldResult:
    get_workspace_or_404(session, workspace_id)
    try:
        return service.calculate_football_field(payload)
    except service.UnderwritingCalculationError as exc:
        raise _calculation_error(exc) from exc
