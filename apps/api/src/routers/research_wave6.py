"""Wave 6 research routes: G65 sum-of-the-parts, G67 litigation, G68 macro MC presets.

Integration note (integrator-owned steps, reported not performed here):
* register this router in ``main.py``'s ``_ROUTER_MODULES``;
* add ``"litigation_service"`` to ``analysis_service``'s risk-flag source tuple
  (``for mod_name in ("forensics_service", "sec_feeds_service")``) so litigation red flags are
  spliced into analysis runs — ``litigation_service.risk_flags`` already matches that contract.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from src.routers.deps import SessionDep
from src.services import litigation_service, macro_preset_service, sotp_service

router = APIRouter(prefix="/api/workspaces", tags=["research-wave6"])


class SotpRequest(BaseModel):
    """Per-segment EV/Revenue multiples for a sum-of-the-parts build (G65).

    ``multiples`` keys match a segment's XBRL member (preferred) or its human segment name.
    Segments with no matching key fall back to ``default_multiple`` when supplied, else they are
    reported unvalued. The unallocated residual is valued ONLY when ``residual_multiple`` is
    supplied — it is never force-balanced.
    """

    multiples: dict[str, float] = Field(default_factory=dict, max_length=50)
    default_multiple: float | None = Field(default=None, gt=0, le=100)
    residual_multiple: float | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def positive_multiples(self):
        for key, value in self.multiples.items():
            if not 0 < value <= 100:
                raise ValueError(f"Multiple for '{key}' must be > 0 and <= 100")
        return self


@router.post("/{workspace_id}/sotp")
def build_sotp(workspace_id: str, payload: SotpRequest, session: SessionDep) -> dict:
    """G65 — sum-of-the-parts over stored G12 segment revenue with an explicit residual."""
    return sotp_service.build(session, workspace_id, payload.model_dump())


@router.get("/{workspace_id}/litigation")
def get_litigation(workspace_id: str, session: SessionDep) -> dict:
    """G67 — Item 3 (Legal Proceedings) excerpts + the explicitly-legal 8-K timeline."""
    return litigation_service.build(session, workspace_id)


@router.get("/{workspace_id}/macro-mc-presets")
def get_macro_mc_presets(workspace_id: str, session: SessionDep) -> dict:
    """G68 — transparent, versioned FRED-to-Monte-Carlo distribution presets (user-editable)."""
    return macro_preset_service.build(session, workspace_id)
