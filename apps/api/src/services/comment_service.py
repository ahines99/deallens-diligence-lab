"""Comment threads with @mentions on any governed artifact (G41).

A comment is a tenant-scoped note attached to an opaque ``(entity_type, entity_id)`` artifact
reference. Writing a comment is a privileged action: viewers are read-only and are denied (mirroring
the middleware viewer guard), and a principal may only comment within its own organization.

``@mentions`` in the body are resolved against the author's active organization members (by email
handle or full email) and stored as user ids. Each resolved mention emits a ``comment.mentioned``
``WorkflowAuditEvent`` through the durable audit outbox; ``notification_service.sync_from_audit``
projects those into directed, per-recipient notifications. A ``comment.created`` event is always
emitted for the thread itself. Non-member mentions are silently ignored (never notified).
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.comment import COMMENT_ENTITY_TYPES, Comment
from src.models.deal_workflow import WorkflowAuditEvent
from src.models.identity import OrganizationMembership, User
from src.schemas.comment import CommentCreate
from src.schemas.identity import PrincipalContext
from src.services.common import NotFound
from src.services.identity_service import IdentityForbidden

# An @mention token: an email (alice@corp.com) or a bare handle (alice, alice.smith, dev-lead).
_MENTION_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9._%+-]*(?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?)")


def _require_write(principal: PrincipalContext) -> None:
    """Viewers are read-only everywhere; commenting is a write, so a viewer is denied (403)."""
    if principal.role == "viewer":
        raise IdentityForbidden("Viewer memberships are read-only and cannot post comments")


def _org_member_handles(session: Session, organization_id: str) -> dict[str, str]:
    """Map every active org member's email + email-local-part handle to their user id."""
    rows = session.execute(
        select(User.id, User.email_normalized)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
            User.status == "active",
        )
    ).all()
    handles: dict[str, str] = {}
    for user_id, email in rows:
        email = (email or "").lower()
        if not email:
            continue
        handles[email] = user_id
        local = email.split("@", 1)[0]
        # First writer wins on a collision so a handle never silently rebinds to another member.
        handles.setdefault(local, user_id)
    return handles


def resolve_mentions(session: Session, organization_id: str, body: str) -> list[str]:
    """Resolve @mentions in ``body`` to a de-duplicated, order-preserving list of member user ids."""
    handles = _org_member_handles(session, organization_id)
    resolved: list[str] = []
    for token in _MENTION_RE.findall(body):
        user_id = handles.get(token.lower())
        if user_id is not None and user_id not in resolved:
            resolved.append(user_id)
    return resolved


def _audit(
    session: Session,
    organization_id: str,
    actor: PrincipalContext | None,
    action: str,
    comment: Comment,
    detail: dict[str, Any] | None = None,
) -> WorkflowAuditEvent:
    """Append an audit event and fan it into the durable webhook outbox (same transaction)."""
    event = WorkflowAuditEvent(
        organization_id=organization_id,
        deal_id=None,
        actor_id=actor.user_id if actor else None,
        actor_display_name=actor.display_name if actor else None,
        action=action,
        entity_type="Comment",
        entity_id=comment.id,
        detail=detail or {},
    )
    session.add(event)
    session.flush()
    from src.services import webhook_service

    webhook_service.queue_for_audit_event(session, event)
    return event


def create_comment(
    session: Session, data: CommentCreate, principal: PrincipalContext
) -> Comment:
    """Create a comment (or threaded reply) as ``principal`` within its own organization."""
    _require_write(principal)
    if data.entity_type not in COMMENT_ENTITY_TYPES:
        raise ValueError(f"Unknown entity_type: {data.entity_type}")

    organization_id = principal.organization_id
    if data.parent_comment_id is not None:
        parent = session.get(Comment, data.parent_comment_id)
        # A reply must target a thread the caller can actually see, on the same artifact.
        if (
            parent is None
            or parent.organization_id != organization_id
            or parent.entity_type != data.entity_type
            or parent.entity_id != data.entity_id
        ):
            raise NotFound(f"Parent comment '{data.parent_comment_id}' not found")

    mentions = resolve_mentions(session, organization_id, data.body)
    comment = Comment(
        organization_id=organization_id,
        author_user_id=principal.user_id,
        author_display_name=principal.display_name,
        entity_type=data.entity_type,
        entity_id=data.entity_id,
        body=data.body,
        parent_comment_id=data.parent_comment_id,
        mentions=mentions,
    )
    session.add(comment)
    session.flush()

    _audit(
        session,
        organization_id,
        principal,
        "comment.created",
        comment,
        {"entity_type": comment.entity_type, "entity_id": comment.entity_id},
    )
    for user_id in mentions:
        _audit(
            session,
            organization_id,
            principal,
            "comment.mentioned",
            comment,
            {
                "entity_type": comment.entity_type,
                "entity_id": comment.entity_id,
                "mentioned_user_id": user_id,
            },
        )
    session.commit()
    session.refresh(comment)
    return comment


def list_comments(
    session: Session, organization_id: str | None, entity_type: str, entity_id: str
) -> list[Comment]:
    """Flat, chronological list of a single artifact's comments, tenant-scoped when known."""
    statement = select(Comment).where(
        Comment.entity_type == entity_type, Comment.entity_id == entity_id
    )
    if organization_id is not None:
        statement = statement.where(Comment.organization_id == organization_id)
    return list(session.scalars(statement.order_by(Comment.created_at, Comment.id)))


def list_thread(
    session: Session, organization_id: str | None, entity_type: str, entity_id: str
) -> list[Comment]:
    """Top-level comments (``parent_comment_id is None``) with their direct replies attached.

    Returns ORM objects; each top-level comment gets a transient ``replies`` attribute so the router
    can serialize a one-level thread without a second query per comment.
    """
    comments = list_comments(session, organization_id, entity_type, entity_id)
    by_parent: dict[str, list[Comment]] = {}
    roots: list[Comment] = []
    for comment in comments:
        if comment.parent_comment_id is None:
            roots.append(comment)
        else:
            by_parent.setdefault(comment.parent_comment_id, []).append(comment)
    for root in roots:
        root.replies = by_parent.get(root.id, [])  # type: ignore[attr-defined]
    return roots


def resolve_comment(
    session: Session, comment_id: str, principal: PrincipalContext
) -> Comment:
    """Mark a comment resolved. Cross-org access is a 404 (non-enumerable tenant boundary)."""
    _require_write(principal)
    comment = session.get(Comment, comment_id)
    if comment is None or comment.organization_id != principal.organization_id:
        raise NotFound(f"Comment '{comment_id}' not found")
    if comment.resolved_at is None:
        comment.resolved_at = now_utc()
        comment.resolved_by_user_id = principal.user_id
        _audit(session, comment.organization_id, principal, "comment.resolved", comment)
        session.commit()
        session.refresh(comment)
    return comment
