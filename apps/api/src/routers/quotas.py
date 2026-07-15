"""Per-organization quota-usage inspection (G39).

Org-scoped like ``portfolio.py``: a principal may only read its own organization's usage; a
cross-tenant read returns 404 rather than acting as an existence oracle.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import OptionalPrincipalDep
from src.schemas.quota import QuotaUsageOut

router = APIRouter(prefix="/api/organizations", tags=["quotas"])


@router.get("/{organization_id}/quota-usage", response_model=QuotaUsageOut)
def get_quota_usage(organization_id: str, principal: OptionalPrincipalDep) -> QuotaUsageOut:
    if principal is not None and principal.organization_id != organization_id:
        # Tenant identifiers are intentionally non-enumerable across memberships.
        raise HTTPException(status_code=404, detail="Organization not found")
    # Imported lazily: the limiter/reporter live in the app module, which imports this router.
    from src.main import org_quota_usage

    return QuotaUsageOut(
        organization_id=organization_id,
        buckets=org_quota_usage(organization_id),
    )


__all__ = ["router"]
