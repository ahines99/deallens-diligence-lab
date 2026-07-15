"""G41 — comment threads with @mentions on any governed artifact.

Covers thread replies, @mention resolution against org members, the mention -> directed
notification fan-out through the audit outbox, viewer read-only permission, cross-org tenant
isolation, resolve semantics, and the HTTP surface (auth-required).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models.deal_workflow import Organization
from src.models.identity import OrganizationMembership, User
from src.schemas.comment import CommentCreate
from src.schemas.identity import PrincipalContext
from src.services import comment_service, notification_service
from src.services.common import NotFound
from src.services.identity_service import IdentityForbidden


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _user(session: Session, handle: str, role: str, organization_id: str) -> User:
    user = User(
        email=f"{handle}@corp.com",
        email_normalized=f"{handle}@corp.com",
        display_name=handle.title(),
        password_hash="x",
    )
    session.add(user)
    session.flush()
    session.add(
        OrganizationMembership(
            user_id=user.id, organization_id=organization_id, role=role, status="active"
        )
    )
    session.flush()
    return user


def _org(session: Session, slug: str) -> Organization:
    organization = Organization(name=slug.title(), slug=slug)
    session.add(organization)
    session.flush()
    return organization


def _principal(user: User, organization_id: str, role: str = "member") -> PrincipalContext:
    return PrincipalContext(
        user_id=user.id,
        session_id="sess",
        email=user.email,
        display_name=user.display_name,
        organization_id=organization_id,
        membership_id="mem",
        role=role,
    )


def _setup(session: Session, slug: str = "acme"):
    organization = _org(session, slug)
    author = _user(session, f"author-{slug}", "member", organization.id)
    alice = _user(session, f"alice-{slug}", "member", organization.id)
    viewer = _user(session, f"viewer-{slug}", "viewer", organization.id)
    session.commit()
    return organization, author, alice, viewer


def test_create_comment_and_threaded_reply(db: Session):
    organization, author, _alice, _viewer = _setup(db)
    principal = _principal(author, organization.id)

    root = comment_service.create_comment(
        db,
        CommentCreate(entity_type="risk", entity_id="risk-1", body="Concern on churn."),
        principal,
    )
    reply = comment_service.create_comment(
        db,
        CommentCreate(
            entity_type="risk",
            entity_id="risk-1",
            body="Agreed, needs cohort data.",
            parent_comment_id=root.id,
        ),
        principal,
    )
    assert reply.parent_comment_id == root.id

    thread = comment_service.list_thread(db, organization.id, "risk", "risk-1")
    assert len(thread) == 1
    assert thread[0].id == root.id
    assert [c.id for c in thread[0].replies] == [reply.id]


def test_reply_to_foreign_parent_is_rejected(db: Session):
    organization, author, _alice, _viewer = _setup(db)
    principal = _principal(author, organization.id)
    root = comment_service.create_comment(
        db, CommentCreate(entity_type="memo", entity_id="memo-1", body="Note."), principal
    )
    # A reply must sit on the same artifact as its parent.
    with pytest.raises(NotFound):
        comment_service.create_comment(
            db,
            CommentCreate(
                entity_type="risk",
                entity_id="risk-9",
                body="mismatched",
                parent_comment_id=root.id,
            ),
            principal,
        )


def test_mention_of_member_is_recorded_and_notifies_the_recipient(db: Session):
    organization, author, alice, _viewer = _setup(db)
    principal = _principal(author, organization.id)

    comment = comment_service.create_comment(
        db,
        CommentCreate(
            entity_type="ic_packet",
            entity_id="packet-1",
            body="Please review @alice-acme — flagged for you.",
        ),
        principal,
    )
    assert comment.mentions == [alice.id]

    created = notification_service.sync_from_audit(db, organization.id)
    mention_notes = [n for n in created if n.event_type == "comment.mentioned"]
    assert len(mention_notes) == 1
    assert mention_notes[0].recipient_user_id == alice.id
    assert mention_notes[0].title == "You were mentioned"
    # The thread itself also projects an (org-wide, unrecipiented) comment.created notification.
    created_notes = [n for n in created if n.event_type == "comment.created"]
    assert len(created_notes) == 1
    assert created_notes[0].recipient_user_id is None


def test_mention_of_non_member_is_ignored(db: Session):
    organization, author, _alice, _viewer = _setup(db)
    principal = _principal(author, organization.id)
    comment = comment_service.create_comment(
        db,
        CommentCreate(
            entity_type="workspace",
            entity_id="ws-1",
            body="cc @stranger and @nobody",
        ),
        principal,
    )
    assert comment.mentions == []
    created = notification_service.sync_from_audit(db, organization.id)
    assert not [n for n in created if n.event_type == "comment.mentioned"]


def test_viewer_is_denied_posting_but_can_read(db: Session):
    organization, author, _alice, viewer = _setup(db)
    comment_service.create_comment(
        db,
        CommentCreate(entity_type="risk", entity_id="risk-1", body="seed"),
        _principal(author, organization.id),
    )
    viewer_principal = _principal(viewer, organization.id, role="viewer")

    with pytest.raises(IdentityForbidden) as exc:
        comment_service.create_comment(
            db,
            CommentCreate(entity_type="risk", entity_id="risk-1", body="viewer note"),
            viewer_principal,
        )
    assert exc.value.status_code == 403

    # A viewer can still read the thread.
    visible = comment_service.list_comments(db, organization.id, "risk", "risk-1")
    assert len(visible) == 1


def test_cross_org_comment_access_is_not_found(db: Session):
    org_a, author_a, _alice_a, _viewer_a = _setup(db, "alpha")
    org_b, author_b, _alice_b, _viewer_b = _setup(db, "beta")
    comment = comment_service.create_comment(
        db,
        CommentCreate(entity_type="risk", entity_id="risk-1", body="alpha only"),
        _principal(author_a, org_a.id),
    )
    # Org B cannot see or resolve org A's comment.
    assert comment_service.list_comments(db, org_b.id, "risk", "risk-1") == []
    with pytest.raises(NotFound):
        comment_service.resolve_comment(db, comment.id, _principal(author_b, org_b.id))


def test_resolve_flips_resolved_at(db: Session):
    organization, author, _alice, _viewer = _setup(db)
    principal = _principal(author, organization.id)
    comment = comment_service.create_comment(
        db, CommentCreate(entity_type="memo", entity_id="memo-1", body="open item"), principal
    )
    assert comment.resolved_at is None

    resolved = comment_service.resolve_comment(db, comment.id, principal)
    assert resolved.resolved_at is not None
    assert resolved.resolved_by_user_id == author.id


def _register(client, label: str) -> dict:
    suffix = uuid.uuid4().hex[:10]
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{label}-{suffix}@example.test",
            "display_name": f"{label.title()} Analyst",
            "password": "correct horse portfolio battery",
            "organization_name": f"{label.title()} Capital {suffix}",
            "organization_slug": f"{label}-capital-{suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_comment_endpoints_via_api(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    owner = _register(client, "commenter")
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    created = client.post(
        "/api/comments",
        json={"entity_type": "risk", "entity_id": "risk-42", "body": "Endpoint check."},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["entity_id"] == "risk-42"

    listed = client.get(
        "/api/comments",
        params={"entity_type": "risk", "entity_id": "risk-42"},
        headers=headers,
    )
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [body["id"]]

    resolved = client.post(f"/api/comments/{body['id']}/resolve", headers=headers)
    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None

    # No credential -> the authored endpoint refuses (author must be server-derived).
    anon = client.post(
        "/api/comments",
        json={"entity_type": "risk", "entity_id": "risk-42", "body": "anon"},
    )
    assert anon.status_code == 401
