"""Effective-capability resolution for a membership (G49).

``effective_capabilities`` = role defaults, then per-membership grants added and revokes removed.
Deny-by-default: a capability absent from the returned set is denied. Kept separate from
:mod:`src.services.identity_service` so both the session path and the API-key path can resolve
capabilities without a circular import.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.identity import OrganizationMembership
from src.models.permission import MembershipPermission
from src.permissions import ALL_CAPABILITIES, role_default_capabilities
from src.schemas.identity import PermissionGrantPatch
from src.services.identity_service import (
    IdentityError,
    effective_capabilities,
    require_admin,
)

# ``effective_capabilities`` lives in identity_service (the authentication path resolves it into
# every principal); re-exported here so the permission API is the one import surface for authz.
__all__ = ["effective_capabilities", "list_membership_permissions", "set_membership_permission"]


def list_membership_permissions(
    session: Session, membership_id: str, principal
) -> dict:
    """Report a membership's role defaults, explicit overrides, and effective capability set."""
    membership = session.get(OrganizationMembership, membership_id)
    if membership is None:
        raise IdentityError("Membership not found", status_code=404)
    require_admin(principal, membership.organization_id)
    overrides = list(
        session.scalars(
            select(MembershipPermission)
            .where(MembershipPermission.membership_id == membership_id)
            .order_by(MembershipPermission.capability)
        )
    )
    return {
        "membership_id": membership_id,
        "role": membership.role,
        "role_defaults": sorted(role_default_capabilities(membership.role)),
        "overrides": [
            {"capability": item.capability, "granted": item.granted} for item in overrides
        ],
        "effective": sorted(effective_capabilities(session, membership)),
    }


def set_membership_permission(
    session: Session, membership_id: str, data: PermissionGrantPatch, principal
) -> dict:
    """Grant or revoke one capability for one membership (owners/admins only)."""
    membership = session.get(OrganizationMembership, membership_id)
    if membership is None:
        raise IdentityError("Membership not found", status_code=404)
    require_admin(principal, membership.organization_id)
    if data.capability not in ALL_CAPABILITIES:
        raise IdentityError(f"Unknown capability: {data.capability}", status_code=400)
    override = session.scalar(
        select(MembershipPermission).where(
            MembershipPermission.membership_id == membership_id,
            MembershipPermission.capability == data.capability,
        )
    )
    if override is None:
        override = MembershipPermission(
            membership_id=membership_id,
            capability=data.capability,
            granted=data.granted,
        )
        session.add(override)
    else:
        override.granted = data.granted
    session.commit()
    return list_membership_permissions(session, membership_id, principal)
