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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import Organization
from src.models.risk import RiskFinding
from src.models.share_link import ShareLink
from src.models.share_link_view import ShareLinkView
from src.models.target import Target
from src.models.workspace import Workspace
from src.schemas.identity import PrincipalContext
from src.schemas.share_link import ShareLinkCreate
from src.services.common import NotFound
from src.services.identity_service import IdentityError

_TOKEN_PREFIX = "dsh_"
# G76 analytics contract: `recent` is capped at the newest 20 events.
_RECENT_VIEWS_CAP = 20
# Mirrors the coarse session context (`AuthSession.user_agent` truncation discipline).
_VIEW_USER_AGENT_MAX = 200
_VIEW_CLIENT_HOST_MAX = 64


class ShareLinkGone(NotFound):
    """A syntactically valid link that is no longer usable (revoked or expired) -> HTTP 410."""

    status_code = 410


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _utc(value: datetime) -> datetime:
    """Normalize to UTC before persisting. SQLite stores wall-time and drops the offset, so
    an un-normalized "+05:00" expiry would silently shift by hours relative to Postgres."""
    return _aware(value).astimezone(timezone.utc)


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
        expires_at=_utc(data.expires_at) if data.expires_at is not None else None,
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


def record_view(
    session: Session,
    share_link: ShareLink,
    *,
    user_agent: str | None = None,
    client_host: str | None = None,
) -> None:
    """Append one view event for a successfully served public snapshot (G76). Best-effort.

    Called only AFTER resolution and snapshot assembly both succeeded — invalid, revoked, or
    expired tokens never reach this point, so they never record a view. The insert runs inside
    a SAVEPOINT and every failure (including the commit) is swallowed: analytics must never
    break the public read. Context stored is deliberately coarse — see
    ``src.models.share_link_view`` for the privacy rationale.
    """
    try:
        with session.begin_nested():
            session.add(
                ShareLinkView(
                    share_link_id=share_link.id,
                    user_agent=(user_agent or "")[:_VIEW_USER_AGENT_MAX] or None,
                    client_host=(client_host or "")[:_VIEW_CLIENT_HOST_MAX] or None,
                )
            )
        session.commit()
    except Exception:  # noqa: BLE001 — best-effort by contract; the share view must survive.
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass


def get_share_link_analytics(
    session: Session, share_link_id: str, principal: PrincipalContext
) -> dict:
    """Owner analytics for one link: view count, first/last seen, newest-first recent events.

    Org-scoped exactly like ``revoke_share_link`` — a foreign-tenant or unknown id is a
    non-enumerable 404. The returned ``share_link`` carries the existing revocation state so
    the UI surfaces views and one-click revoke together.
    """
    record = session.get(ShareLink, share_link_id)
    if record is None or record.organization_id != principal.organization_id:
        raise NotFound(f"Share link '{share_link_id}' not found")

    scoped = ShareLinkView.share_link_id == share_link_id
    view_count = session.scalar(select(func.count()).select_from(ShareLinkView).where(scoped)) or 0
    first_viewed_at = session.scalar(select(func.min(ShareLinkView.viewed_at)).where(scoped))
    last_viewed_at = session.scalar(select(func.max(ShareLinkView.viewed_at)).where(scoped))
    recent = list(
        session.scalars(
            select(ShareLinkView)
            .where(scoped)
            .order_by(ShareLinkView.viewed_at.desc(), ShareLinkView.id.desc())
            .limit(_RECENT_VIEWS_CAP)
        )
    )
    return {
        "share_link": record,
        "view_count": int(view_count),
        "first_viewed_at": first_viewed_at,
        "last_viewed_at": last_viewed_at,
        "recent": [{"viewed_at": v.viewed_at, "user_agent": v.user_agent} for v in recent],
    }


def compose_watermark(session: Session, share_link: ShareLink) -> str:
    """Server-composed watermark line for the shared render (G76).

    Composed on the server so a client cannot omit it by ignoring a boolean flag — the text IS
    the payload field. This is a provenance deterrent (who shared, which link, when), not DRM:
    a determined viewer can always screenshot around any client-side overlay. The link is
    identified by a digest prefix — the plaintext token is never persisted (G44), and eight hex
    chars of SHA-256 identify the row without weakening the secret.
    """
    organization = session.get(Organization, share_link.organization_id)
    org_name = organization.name if organization is not None else "DealLens"
    created = share_link.created_at.date().isoformat() if share_link.created_at else "n/a"
    return (
        f"Shared read-only · {org_name} · link {share_link.token_digest[:8]}"
        f" · {created}"
    )


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
        "watermark": compose_watermark(session, share_link),
    }
