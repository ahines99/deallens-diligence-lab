"""Authentication and organization-membership API contracts."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @property
    def actor_roles(self) -> tuple[str, ...]:
        roles = [self.role]
        if self.role in {"owner", "admin"}:
            roles.extend(["organization_admin", "integration_admin"])
        return tuple(roles)


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


class WorkspaceGovernancePatch(StrictModel):
    data_classification: Literal["public", "internal", "confidential", "restricted"] | None = None
    external_llm_allowed: bool | None = None

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one governance setting is required")
        return self
