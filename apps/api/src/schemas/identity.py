"""Authentication and organization-membership API contracts."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.permissions import role_default_capabilities
from src.schemas.common import ORMModel

MembershipRole = Literal["owner", "admin", "member", "viewer"]
MembershipStatus = Literal["active", "suspended"]
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalize_email(value: str) -> str:
    value = value.lower()
    if not _EMAIL.fullmatch(value):
        raise ValueError("email must be a valid address")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PrincipalContext(StrictModel):
    user_id: str
    session_id: str
    email: str
    display_name: str
    organization_id: str
    membership_id: str
    role: MembershipRole
    # ``None`` for human sessions and trusted-service callers (unrestricted, gated only by role).
    # A tuple for API-key principals (G38): the key may only exercise these granted scopes.
    scopes: tuple[str, ...] | None = None
    # Effective fine-grained capabilities (G49), resolved at authentication from the membership's
    # role defaults ± per-membership grants/revokes. ``None`` means "not resolved" — callers fall
    # back to the role defaults so principals minted before capability resolution still work.
    capabilities: tuple[str, ...] | None = None

    @property
    def actor_roles(self) -> tuple[str, ...]:
        roles = [self.role]
        if self.role in {"owner", "admin"}:
            roles.extend(["organization_admin", "integration_admin"])
        return tuple(roles)

    @property
    def is_api_key(self) -> bool:
        return self.scopes is not None

    @property
    def is_service_account(self) -> bool:
        """True for the trusted-service (internal token) path, whose actor id is caller-chosen.

        Four-eyes review planes must reject these principals as reviewers: the header-claimed
        actor id would let automation "review" its own proposal under a second name.
        """
        return self.session_id == "trusted-service"

    def has_scope(self, scope: str) -> bool:
        """Human/service principals (``scopes is None``) are unrestricted; keys need the grant."""
        return self.scopes is None or scope in self.scopes

    def effective_capabilities(self) -> frozenset[str]:
        """The resolved capability set, defaulting to the role's defaults when unresolved."""
        if self.capabilities is not None:
            return frozenset(self.capabilities)
        return role_default_capabilities(self.role)

    def has_capability(self, capability: str) -> bool:
        """Deny-by-default: a capability outside the effective set is denied (G49)."""
        return capability in self.effective_capabilities()


class RegistrationCreate(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)
    organization_name: str = Field(min_length=1, max_length=200)
    organization_slug: str = Field(min_length=2, max_length=100)

    @field_validator("organization_slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("organization_slug must use lowercase letters, numbers, and hyphens")
        return value

    _validate_email = field_validator("email")(_normalize_email)


class LoginCreate(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    organization_id: str | None = Field(default=None, max_length=32)

    _validate_email = field_validator("email")(_normalize_email)


class OrganizationSwitch(StrictModel):
    organization_id: str = Field(min_length=32, max_length=32)


class UserOut(ORMModel):
    id: str
    email: str
    display_name: str
    status: str
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MembershipOut(ORMModel):
    id: str
    user_id: str
    organization_id: str
    role: MembershipRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
    email: str | None = None
    display_name: str | None = None


class SessionTokenOut(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    principal: PrincipalContext
    memberships: list[MembershipOut]


class CurrentIdentityOut(StrictModel):
    principal: PrincipalContext
    memberships: list[MembershipOut]


class MembershipCreate(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    role: MembershipRole = "member"

    _validate_email = field_validator("email")(_normalize_email)


class MembershipPatch(StrictModel):
    role: MembershipRole | None = None
    status: MembershipStatus | None = None

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("membership updates cannot be null")
        return self


class LogoutOut(StrictModel):
    revoked: bool


class OIDCLoginOut(StrictModel):
    """Authorize-URL + opaque ``state`` the client redirects the browser to (G48)."""

    authorize_url: str
    state: str


class PermissionGrantPatch(StrictModel):
    """Grant (``granted=true``) or revoke (``granted=false``) one capability for a membership."""

    capability: str = Field(min_length=1, max_length=60)
    granted: bool = True


class PermissionOverrideOut(StrictModel):
    capability: str
    granted: bool


class MembershipPermissionsOut(StrictModel):
    """A membership's role defaults, explicit overrides, and resolved effective capability set."""

    membership_id: str
    role: MembershipRole
    role_defaults: list[str]
    overrides: list[PermissionOverrideOut]
    effective: list[str]


class WorkspaceGovernancePatch(StrictModel):
    data_classification: Literal["public", "internal", "confidential", "restricted"] | None = None
    external_llm_allowed: bool | None = None

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one governance setting is required")
        return self
