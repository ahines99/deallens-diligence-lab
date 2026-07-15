"""Scoped API-key API contracts (G38)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from src.schemas.common import ORMModel
from src.schemas.identity import StrictModel


class ApiKeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def _dedupe_scopes(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("at least one scope is required")
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(cleaned))


class ApiKeyOut(ORMModel):
    id: str
    organization_id: str
    created_by_user_id: str | None
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApiKeyCreatedOut(StrictModel):
    """Returned once on creation — the only time the plaintext secret is ever exposed."""

    api_key: ApiKeyOut
    plaintext_key: str
