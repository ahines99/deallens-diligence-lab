"""Wave 6 Theme J market-context endpoints: frames peer benchmarking (G64) + dilution (G66).

Kept in a dedicated router (mirroring ownership.py) so the market-context feeds evolve
independently. Both endpoints compute on demand from keyless SEC data and preserve the explicit
status discipline (available / partial / unavailable): an upstream EDGAR outage degrades to
``unavailable`` with a ``source_error``, never a false-clean empty.

NOTE for the integrator: this router must be added to ``_ROUTER_MODULES`` in ``src/main.py``
(import ``peer_benchmark`` from ``src.routers``) — it is not self-registering.
"""
from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.dilution import DilutionAnalysis
from src.schemas.peer_benchmark import PeerBenchmark
from src.services import dilution_service, peer_benchmark_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["market-context"])


@router.get("/{workspace_id}/peer-benchmark", response_model=PeerBenchmark)
def get_peer_benchmark(workspace_id: str, session: SessionDep) -> PeerBenchmark:
    """Percentile ranks vs the SEC XBRL frames reporting universe, with coverage counts.

    Honest scoping: frames carry no SIC codes, so percentiles are ranked against ALL US filers
    reporting the concept for the frame year (labeled in ``peer_scope``); thin coverage degrades
    to an explicit "insufficient peer coverage" — a percentile is never fabricated.
    """
    get_workspace_or_404(session, workspace_id)
    return PeerBenchmark.model_validate(peer_benchmark_service.build(session, workspace_id))


@router.get("/{workspace_id}/dilution", response_model=DilutionAnalysis)
def get_dilution(workspace_id: str, session: SessionDep) -> DilutionAnalysis:
    """Per-fiscal-year buyback & dilution derivation from XBRL company facts, with citations.

    CY-frame keyed (``fy`` is never a period key); a concept the filer did not tag for a year is
    ``None`` for that field-year — reported as missing, never interpolated.
    """
    get_workspace_or_404(session, workspace_id)
    return DilutionAnalysis.model_validate(dilution_service.dilution(session, workspace_id))
