"""Per-organization quota-usage inspection (G39) with the G80 LLM spend rollup beside it.

Org-scoped like ``portfolio.py``: a principal may only read its own organization's usage; a
cross-tenant read returns 404 rather than acting as an existence oracle.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import OptionalPrincipalDep, SessionDep
from src.schemas.quota import LlmSpendOut, QuotaUsageOut
from src.services import llm_usage_service

# The spend rollup shares the endpoint's read semantics but is windowed: the last day of live
# token usage sits beside the rate buckets as "what has this org actually spent recently".
LLM_SPEND_WINDOW_HOURS = 24

router = APIRouter(prefix="/api/organizations", tags=["quotas"])


@router.get("/{organization_id}/quota-usage", response_model=QuotaUsageOut)
def get_quota_usage(
    organization_id: str, principal: OptionalPrincipalDep, session: SessionDep
) -> QuotaUsageOut:
    if principal is not None and principal.organization_id != organization_id:
        # Tenant identifiers are intentionally non-enumerable across memberships.
        raise HTTPException(status_code=404, detail="Organization not found")
    # Imported lazily: the limiter/reporter live in the app module, which imports this router.
    from src.main import org_quota_usage

    spend = llm_usage_service.spend_summary(
        session, organization_id, window_hours=LLM_SPEND_WINDOW_HOURS
    )
    return QuotaUsageOut(
        organization_id=organization_id,
        buckets=org_quota_usage(organization_id),
        llm_spend=LlmSpendOut(window_hours=LLM_SPEND_WINDOW_HOURS, **spend),
    )


__all__ = ["router"]
