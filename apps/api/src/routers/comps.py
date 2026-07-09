from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.comp import CompOut, CompsRequest, FinancialBenchmark
from src.services import analysis_service, financial_benchmark_service as bench
from src.services import workspace_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["comps"])


@router.post("/{workspace_id}/comps", response_model=list[CompOut])
def set_comps(workspace_id: str, payload: CompsRequest, session: SessionDep) -> list[CompOut]:
    get_workspace_or_404(session, workspace_id)
    if payload.comps:
        comps = bench.add_comps(session, workspace_id, payload.comps)
    else:
        comps = bench.add_comps_by_ticker(session, workspace_id, payload.tickers)
    session.commit()
    # Refresh the memo/benchmark so it reflects the new peer set.
    if workspace_service.get_target(session, workspace_id) is not None:
        analysis_service.run_full_analysis(session, workspace_id)
    return [CompOut.model_validate(c) for c in bench.list_comps(session, workspace_id)]


@router.get("/{workspace_id}/comps", response_model=list[CompOut])
def list_comps(workspace_id: str, session: SessionDep) -> list[CompOut]:
    get_workspace_or_404(session, workspace_id)
    return [CompOut.model_validate(c) for c in bench.list_comps(session, workspace_id)]


@router.get("/{workspace_id}/benchmark", response_model=FinancialBenchmark)
def get_benchmark(workspace_id: str, session: SessionDep) -> FinancialBenchmark:
    get_workspace_or_404(session, workspace_id)
    return FinancialBenchmark.model_validate(bench.compute_benchmark(session, workspace_id))
