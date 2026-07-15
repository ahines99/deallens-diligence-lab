"""Read-only tokenized share links for a frozen workspace snapshot (G44).

Tokens are opaque ``dsh_<random>`` secrets; only the SHA-256 digest is stored (mirroring the
revocable-session and API-key designs), and the plaintext is returned exactly once at creation.
Resolution succeeds only while the link is unexpired and unrevoked; a revoked or expired link raises
``ShareLinkGone`` (410) and an unknown token raises ``NotFound`` (404).

Safe default of what is shared: the ``read_only`` scope exposes a deliberately narrow,
**non-confidential** snapshot — the workspace's public identity, the target's public-company
identity (name/ticker/sector/description), and the risk findings (analytical research output) plus
artifact counts. It intentionally excludes confidential financial line items, valuation numbers, QoE
adjustments, memo bodies, and any data-room content. A link never widens beyond its workspace.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.risk import RiskFinding
from src.models.share_link import ShareLink
from src.models.target import Target
from src.models.workspace import Workspace
from src.schemas.identity import PrincipalContext
from src.schemas.share_link import ShareLinkCreate
from src.services.common import NotFound
from src.services.identity_service import IdentityError

_TOKEN_PREFIX = "dsh_"


class ShareLinkGone(NotFound):
    """A syntactically valid link that is no longer usable (revoked or expired) -> HTTP 410."""

    status_code = 410


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def create_share_link(
    session: Session,
    workspace_id: str,
    data: ShareLinkCreate,
    principal: PrincipalContext,
) -> tuple[ShareLink, str]:
    """Mint a share link for a workspace the principal owns. Returns the record + plaintext token."""
    workspace = session.get(Workspace, workspace_id)
    # Cross-tenant / unknown workspace is a non-enumerable 404, like the rest of the tenant surface.
    if workspace is None or workspace.organization_id != principal.organization_id:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    if data.expires_at is not None and _aware(data.expires_at) <= now_utc():
        raise IdentityError("expires_at must be in the future", status_code=400)

    raw_token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = ShareLink(
        organization_id=principal.organization_id,
        workspace_id=workspace_id,
        token_digest=_digest(raw_token),
        created_by_user_id=None if principal.is_api_key else principal.user_id,
        scope=data.scope,
        label=data.label,
        expires_at=data.expires_at,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, raw_token


def resolve_share_link(session: Session, raw_token: str) -> ShareLink:
    """Resolve a ``dsh_`` token to its live link, or raise (404 unknown / 410 revoked-or-expired)."""
    if not raw_token.startswith(_TOKEN_PREFIX) or len(raw_token) < 16:
        raise NotFound("Share link not found")
    record = session.scalar(select(ShareLink).where(ShareLink.token_digest == _digest(raw_token)))
    if record is None:
        raise NotFound("Share link not found")
    if record.revoked_at is not None:
        raise ShareLinkGone("Share link has been revoked")
    if record.expires_at is not None and _aware(record.expires_at) <= now_utc():
        raise ShareLinkGone("Share link has expired")
    # Best-effort access stamp; never blocks the read.
    record.last_accessed_at = now_utc()
    session.commit()
    session.refresh(record)
    return record


def list_share_links(
    session: Session, workspace_id: str, principal: PrincipalContext
) -> list[ShareLink]:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None or workspace.organization_id != principal.organization_id:
        raise NotFound(f"Workspace '{workspace_id}' not found")
    return list(
        session.scalars(
            select(ShareLink)
            .where(ShareLink.workspace_id == workspace_id)
            .order_by(ShareLink.created_at.desc())
        )
    )


def revoke_share_link(
    session: Session, share_link_id: str, principal: PrincipalContext
) -> ShareLink:
    record = session.get(ShareLink, share_link_id)
    if record is None or record.organization_id != principal.organization_id:
        raise NotFound(f"Share link '{share_link_id}' not found")
    if record.revoked_at is None:
        record.revoked_at = now_utc()
        session.commit()
        session.refresh(record)
    return record


def build_snapshot(session: Session, share_link: ShareLink) -> dict:
    """Assemble the non-confidential read-only snapshot the token unlocks (safe default).

    Only public-derived research artifacts are included. Confidential financials, valuation figures,
    QoE adjustments, memo narratives, and data-room documents are intentionally omitted regardless of
    the workspace's own ``data_classification``.
    """
    workspace = session.get(Workspace, share_link.workspace_id)
    if workspace is None:
        raise NotFound("Workspace not found")

    target = session.scalar(select(Target).where(Target.workspace_id == workspace.id))
    target_view = None
    if target is not None:
        # Public-company identity only — never the numeric ``financials`` extract.
        target_view = {
            "name": target.name,
            "ticker": target.ticker,
            "sector": target.sector,
            "description": target.description,
            "target_type": target.target_type,
        }

    risks = list(
        session.scalars(
            select(RiskFinding)
            .where(RiskFinding.workspace_id == workspace.id)
            .order_by(RiskFinding.severity_score.desc())
        )
    )
    risk_views = [
        {
            "title": r.title,
            "category": r.risk_category,
            "category_label": r.risk_category_label,
            "severity": r.severity,
            "severity_score": r.severity_score,
        }
        for r in risks
    ]

    return {
        "scope": share_link.scope,
        "workspace": {
            "name": workspace.name,
            "deal_type": workspace.deal_type,
            "status": workspace.status,
            "investment_question": workspace.investment_question,
        },
        "target": target_view,
        "risks": risk_views,
        "counts": {"risks": len(risk_views)},
        "disclaimer": (
            "Read-only shared snapshot. Confidential financials, valuation detail, and data-room "
            "content are excluded. Not investment advice."
        ),
    }
