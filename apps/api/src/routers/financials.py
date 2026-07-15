from __future__ import annotations

from fastapi import APIRouter

from src.db.base import now_utc
from src.routers.deps import SessionDep
from src.schemas.macro import MacroOverlay
from src.schemas.quarterly import QuarterlyFinancials
from src.schemas.segments import SegmentRevenue
from src.schemas.trends import FinancialTrends
from src.services import financial_benchmark_service as bench
from src.services import fred_service, workspace_service
from src.services.common import NotFound, get_workspace_or_404
from src.services.sec_financials import QUARTERLY_METRICS

router = APIRouter(prefix="/api/workspaces", tags=["financials"])


@router.get("/{workspace_id}/trends", response_model=FinancialTrends)
def get_trends(workspace_id: str, session: SessionDep) -> FinancialTrends:
    get_workspace_or_404(session, workspace_id)
    return FinancialTrends.model_validate(bench.get_trends(session, workspace_id))


@router.get("/{workspace_id}/financials/quarterly", response_model=QuarterlyFinancials)
def get_quarterly(workspace_id: str, session: SessionDep) -> QuarterlyFinancials:
    """Last 8 quarters + TTM, computed at ingestion from XBRL and stored on the target.

    Workspaces ingested before quarterly extraction existed have no stored key and return an
    explicit ``source_status: "unavailable"`` (refresh required) instead of a false-clean empty.
    """
    get_workspace_or_404(session, workspace_id)
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    quarterly = (target.financials or {}).get("quarterly")
    if quarterly is None:
        return QuarterlyFinancials.model_validate(
            {
                "workspace_id": workspace_id,
                "target_name": target.name,
                "source_status": "unavailable",
                "source_note": (
                    "Quarterly XBRL extraction is not stored for this workspace — "
                    "refresh (re-ingest) required."
                ),
                "quarters": [],
                "ttm": {key: None for key in QUARTERLY_METRICS},
                "ttm_basis": {},
                "generated_at": now_utc(),
            }
        )
    return QuarterlyFinancials.model_validate(
        {
            "workspace_id": workspace_id,
            "target_name": target.name,
            "source_status": "available",
            "source_note": None,
            "quarters": quarterly.get("quarters", []),
            "ttm": quarterly.get("ttm", {}),
            "ttm_basis": quarterly.get("ttm_basis", {}),
            "generated_at": now_utc(),
        }
    )


@router.get("/{workspace_id}/financials/segments", response_model=SegmentRevenue)
def get_segments(workspace_id: str, session: SessionDep) -> SegmentRevenue:
    """Per-segment revenue trend from dimensional XBRL facts, computed at ingestion.

    Standard SEC company facts publish only consolidated totals, so most real filers return
    ``source_status: "unavailable"`` (segment detail not in companyfacts) — segment splits are
    never fabricated. Workspaces ingested before this feature have no stored key and likewise
    return ``unavailable`` (refresh required).
    """
    get_workspace_or_404(session, workspace_id)
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    segments = (target.financials or {}).get("segments")
    if segments is None:
        return SegmentRevenue.model_validate(
            {
                "workspace_id": workspace_id,
                "target_name": target.name,
                "source_status": "unavailable",
                "source_note": (
                    "Segment XBRL extraction is not stored for this workspace — "
                    "refresh (re-ingest) required."
                ),
                "axis": None,
                "segments": [],
                "generated_at": now_utc(),
            }
        )
    return SegmentRevenue.model_validate(
        {
            "workspace_id": workspace_id,
            "target_name": target.name,
            "source_status": segments.get("status", "unavailable"),
            "source_note": segments.get("note"),
            "axis": segments.get("axis"),
            "segments": segments.get("segments", []),
            "generated_at": now_utc(),
        }
    )


@router.get("/{workspace_id}/macro", response_model=MacroOverlay)
def get_macro(workspace_id: str, session: SessionDep) -> MacroOverlay:
    get_workspace_or_404(session, workspace_id)
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")
    series = fred_service.macro_for_sector(target.sector)
    return MacroOverlay.model_validate(
        {
            "workspace_id": workspace_id,
            "target_name": target.name,
            "sector": target.sector,
            "commentary": fred_service.commentary(series),
            "series": series,
            "generated_at": now_utc(),
        }
    )
