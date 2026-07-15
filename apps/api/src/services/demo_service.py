"""Public-demo posture: one-click guest sessions and retention cleanup.

Only active when ``DEMO_MODE=true``. Guests get a real, short-lived account inside a
shared "Demo Sandbox" organization — the same auth, tenancy, and governance paths as
any user, so nothing in the demo bypasses the product's security model.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import settings
from src.models import Organization, User, Workspace
from src.models.deal_workflow import Deal
from src.models.identity import AuthSession, OrganizationMembership
from src.schemas.identity import SessionTokenOut
from src.db.base import now_utc
from src.services import identity_service
from src.services.identity_service import IdentityForbidden

DEMO_ORG_NAME = "Demo Sandbox"
DEMO_ORG_SLUG = "demo-sandbox"
_GUEST_EMAIL_DOMAIN = "demo.deallens.local"


def _require_demo_mode() -> None:
    if not settings.demo_mode:
        raise IdentityForbidden("Demo mode is not enabled on this deployment")


def _demo_organization(session: Session) -> Organization:
    organization = session.scalar(
        select(Organization).where(Organization.slug == DEMO_ORG_SLUG)
    )
    if organization is None:
        organization = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG)
        session.add(organization)
        session.flush()
    return organization


def start_guest_session(
    session: Session,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokenOut:
    """Mint a fresh guest identity in the shared demo organization."""
    _require_demo_mode()
    organization = _demo_organization(session)
    suffix = secrets.token_hex(6)
    user = User(
        email=f"guest-{suffix}@{_GUEST_EMAIL_DOMAIN}",
        email_normalized=f"guest-{suffix}@{_GUEST_EMAIL_DOMAIN}",
        display_name=f"Guest {suffix[:4].upper()}",
        # Random secret, never disclosed: guests cannot log back in, only continue a session.
        password_hash=identity_service._password_hash(secrets.token_urlsafe(24)),
    )
    session.add(user)
    session.flush()
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=organization.id,
        role="member",
        status="active",
    )
    session.add(membership)
    session.flush()
    raw_token, auth_session = identity_service._new_session(
        session, user, membership, user_agent=user_agent, ip_address=ip_address
    )
    user.last_login_at = now_utc()
    session.commit()
    return identity_service._token_response(session, raw_token, user, membership, auth_session)


def purge_expired_demo_data(session: Session) -> dict:
    """Delete demo-sandbox deals/workspaces and guest identities past the retention window.

    Runs at the database layer (connection-level) because governed tables are deliberately
    append-only at the ORM layer; retention cleanup of an expired demo tenant is the one
    sanctioned destructive maintenance path, and it never touches non-demo organizations.
    """
    # Hard guard: this raw-SQL delete path only ever runs on a deployment that opted into demo
    # mode, so scheduling the cleanup worker elsewhere cannot destroy a real org that happens
    # to share the demo slug.
    _require_demo_mode()
    cutoff = now_utc() - timedelta(hours=max(settings.demo_retention_hours, 1))
    organization = session.scalar(
        select(Organization).where(Organization.slug == DEMO_ORG_SLUG)
    )
    counts = {"workspaces": 0, "deals": 0, "guest_users": 0, "expired_sessions": 0}

    if organization is not None:
        workspace_ids = list(
            session.scalars(
                select(Workspace.id).where(
                    Workspace.organization_id == organization.id,
                    Workspace.created_at < cutoff,
                )
            )
        )
        deal_ids = list(
            session.scalars(
                select(Deal.id).where(
                    Deal.organization_id == organization.id,
                    Deal.created_at < cutoff,
                )
            )
        )
        connection = session.connection()
        if workspace_ids:
            # Underwriting case tables RESTRICT workspace deletion by design; expired demo
            # rows are removed first so the workspace cascade can proceed.
            for table in ("underwriting_case_decisions", "underwriting_case_versions"):
                connection.execute(
                    sa.text(f"DELETE FROM {table} WHERE workspace_id IN :ids").bindparams(
                        sa.bindparam("ids", expanding=True, value=workspace_ids)
                    )
                )
        if deal_ids:
            connection.execute(
                sa.text("DELETE FROM deals WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True, value=deal_ids)
                )
            )
        if workspace_ids:
            connection.execute(
                sa.text("DELETE FROM workspaces WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True, value=workspace_ids)
                )
            )
        counts["workspaces"] = len(workspace_ids)
        counts["deals"] = len(deal_ids)

    # Guest deletion is bounded to actual members of the demo organization, not the email
    # pattern alone — a real (admin-registered) user who happens to match the pattern is never
    # deleted. Guests only ever hold a membership in the demo org, so this is exact.
    guest_ids: list[str] = []
    if organization is not None:
        guest_ids = list(
            session.scalars(
                select(User.id).where(
                    User.email_normalized.like(f"guest-%@{_GUEST_EMAIL_DOMAIN}"),
                    User.created_at < cutoff,
                    User.id.in_(
                        select(OrganizationMembership.user_id).where(
                            OrganizationMembership.organization_id == organization.id
                        )
                    ),
                )
            )
        )
    if guest_ids:
        session.connection().execute(
            sa.text("DELETE FROM users WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True, value=guest_ids)
            )
        )
        counts["guest_users"] = len(guest_ids)

    # Typed ORM delete so the aware cutoff is compared correctly against the timezone-aware
    # column on both SQLite and Postgres (a naive comparand shifts the window on a non-UTC server).
    expired = session.execute(
        sa_delete(AuthSession).where(AuthSession.expires_at < cutoff)
    )
    counts["expired_sessions"] = expired.rowcount or 0

    session.commit()
    return counts
