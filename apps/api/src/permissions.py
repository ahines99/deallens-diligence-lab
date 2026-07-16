"""Fine-grained capability catalog and role defaults (G49).

Deny-by-default authorization layered *over* the four coarse membership roles. A capability that
is not in a principal's effective set is denied. The role defaults below reproduce the existing
coarse behavior (viewers are read-only; members may write but not decide/approve or administer;
admins administer; owners own everything), while per-membership grants/revokes (persisted in
``membership_permissions`` and resolved in :mod:`src.services.permission_service`) let a single
membership be lifted above or dropped below its role without minting a new role.

This module is intentionally dependency-free (standard library only) so both the Pydantic schema
layer (:class:`src.schemas.identity.PrincipalContext`) and the service layer can import it without
creating an import cycle.
"""
from __future__ import annotations

# Canonical capability catalog. Keep authoritative — grants/revokes of an unknown capability are
# meaningless (deny-by-default already rejects it) and the resolution never invents capabilities.
CAPABILITIES: tuple[str, ...] = (
    "workspace:read",
    "workspace:write",
    "underwriting:read",
    "underwriting:write",
    "underwriting:approve",
    "ic:decide",
    "governance:manage",
    "member:manage",
    "apikey:manage",
    "organization:manage",
)

ALL_CAPABILITIES: frozenset[str] = frozenset(CAPABILITIES)

# Read-only surface shared by every role, including viewers.
_READ_ONLY: frozenset[str] = frozenset({"workspace:read", "underwriting:read"})
# Ordinary contributor: read + author working artifacts, but NOT approve/decide or administer.
_MEMBER: frozenset[str] = _READ_ONLY | {"workspace:write", "underwriting:write"}
# Administrators additionally approve/decide and administer members, keys, and governance.
_ADMIN: frozenset[str] = _MEMBER | {
    "underwriting:approve",
    "ic:decide",
    "governance:manage",
    "member:manage",
    "apikey:manage",
}

# Deny-by-default role → capability mapping. Owner is the only role holding ``organization:manage``
# (the sole owner-exclusive capability), so admin is "most" rather than "all".
ROLE_DEFAULTS: dict[str, frozenset[str]] = {
    "owner": ALL_CAPABILITIES,
    "admin": _ADMIN,
    "member": _MEMBER,
    "viewer": _READ_ONLY,
}


def role_default_capabilities(role: str) -> frozenset[str]:
    """Capabilities a role holds before any per-membership grant/revoke override.

    An unrecognized role resolves to the empty set (deny-by-default), never to a permissive one.
    """
    return ROLE_DEFAULTS.get(role, frozenset())
