"""Scoped API keys for programmatic access (G38).

Keys are opaque ``dlk_<random>`` secrets. Only the SHA-256 digest is stored, mirroring the
revocable-session design in :mod:`src.services.identity_service`; the plaintext is returned
exactly once at creation. Authentication resolves a key to a scoped :class:`PrincipalContext`
that the tenant guard treats like any other member principal — the granted scopes only narrow
what the key may do.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.api_key import ApiKey
from src.schemas.api_key import ApiKeyCreate
from src.schemas.identity import PrincipalContext
from src.services.identity_service import (
    IdentityError,
    IdentityUnauthorized,
    require_admin,
)

_KEY_PREFIX = "dlk_"
# The visible id shown in listings: ``dlk_`` + 8 chars of the random body (non-secret).
_VISIBLE_PREFIX_LEN = len(_KEY_PREFIX) + 8
# Throttle last_used_at writes so authenticating every request does not thrash the row.
_LAST_USED_THROTTLE = timedelta(minutes=1)

# Canonical scope catalog. ``read:*`` grants safe/GET access; ``write:*`` is required for the
# corresponding mutating endpoints. Keep this list authoritative — creation rejects unknowns.
API_SCOPES: tuple[str, ...] = (
    "read:workspaces",
    "read:filings",
    "read:financials",
    "read:underwriting",
    "write:underwriting",
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _digest(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("ascii")).hexdigest()


def create_api_key(
    session: Session,
    organization_id: str,
    data: ApiKeyCreate,
    principal: PrincipalContext,
) -> tuple[ApiKey, str]:
    """Mint a key for ``organization_id``. Returns the record and the plaintext (shown once)."""
    require_admin(principal, organization_id)
    unknown = [scope for scope in data.scopes if scope not in API_SCOPES]
    if unknown:
        raise IdentityError(f"Unknown scope(s): {', '.join(sorted(unknown))}", status_code=400)
    if data.expires_at is not None and _aware(data.expires_at) <= now_utc():
        raise IdentityError("expires_at must be in the future", status_code=400)

    raw_key = _KEY_PREFIX + secrets.token_urlsafe(32)
    record = ApiKey(
        organization_id=organization_id,
        created_by_user_id=principal.user_id if not principal.is_api_key else None,
        name=data.name,
        key_prefix=raw_key[:_VISIBLE_PREFIX_LEN],
        key_digest=_digest(raw_key),
        scopes=list(data.scopes),
        expires_at=data.expires_at,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, raw_key


def authenticate_api_key(session: Session, raw_key: str) -> PrincipalContext:
    """Resolve a ``dlk_`` secret to a scoped member principal, or raise 401."""
    if not raw_key.startswith(_KEY_PREFIX) or len(raw_key) < 16:
        raise IdentityUnauthorized()
    record = session.scalar(select(ApiKey).where(ApiKey.key_digest == _digest(raw_key)))
    now = now_utc()
    if (
        record is None
        or record.revoked_at is not None
        or (record.expires_at is not None and _aware(record.expires_at) <= now)
    ):
        raise IdentityUnauthorized()
    if record.last_used_at is None or _aware(record.last_used_at) < now - _LAST_USED_THROTTLE:
        record.last_used_at = now
        session.commit()
    return PrincipalContext(
        user_id=record.created_by_user_id or f"apikey:{record.id}",
        session_id=f"api-key:{record.id}",
        email=f"{record.key_prefix}@api-key.invalid",
        display_name=record.name,
        organization_id=record.organization_id,
        membership_id=f"api-key:{record.id}",
        role="member",
        scopes=tuple(record.scopes or ()),
    )


def list_api_keys(
    session: Session, organization_id: str, principal: PrincipalContext
) -> list[ApiKey]:
    require_admin(principal, organization_id)
    return list(
        session.scalars(
            select(ApiKey)
            .where(ApiKey.organization_id == organization_id)
            .order_by(ApiKey.created_at.desc())
        )
    )


def revoke_api_key(session: Session, key_id: str, principal: PrincipalContext) -> ApiKey:
    record = session.get(ApiKey, key_id)
    if record is None:
        raise IdentityError("API key not found", status_code=404)
    require_admin(principal, record.organization_id)
    if record.revoked_at is None:
        record.revoked_at = now_utc()
        session.commit()
        session.refresh(record)
    return record


def api_key_payload(record: ApiKey) -> dict[str, Any]:
    return {
        "id": record.id,
        "organization_id": record.organization_id,
        "created_by_user_id": record.created_by_user_id,
        "name": record.name,
        "key_prefix": record.key_prefix,
        "scopes": list(record.scopes or ()),
        "last_used_at": record.last_used_at,
        "revoked_at": record.revoked_at,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
