"""Password authentication, revocable opaque sessions, and membership authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import now_utc
from src.models.deal_workflow import Organization
from src.models.identity import AuthSession, OrganizationMembership, User
from src.schemas.identity import (
    LoginCreate,
    MembershipCreate,
    MembershipPatch,
    PrincipalContext,
    RegistrationCreate,
    SessionTokenOut,
)

_PASSWORD_ITERATIONS = 600_000
_TOKEN_PREFIX = "dls_"
_DUMMY_SALT = b"DealLens-auth-dummy-salt"


class IdentityError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class IdentityUnauthorized(IdentityError):
    def __init__(self, message: str = "Invalid or expired authentication") -> None:
        super().__init__(message, status_code=401)


class IdentityForbidden(IdentityError):
    def __init__(self, message: str = "Organization role does not permit this operation") -> None:
        super().__init__(message, status_code=403)


class IdentityConflict(IdentityError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PASSWORD_ITERATIONS)
    return "$".join(
        (
            "pbkdf2-sha256",
            str(_PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        )
    )


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2-sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _b64decode(salt), int(iterations)
        )
        return hmac.compare_digest(actual, _b64decode(expected))
    except (ValueError, TypeError):
        return False


def _dummy_password_check(password: str) -> None:
    hashlib.pbkdf2_hmac("sha256", password.encode(), _DUMMY_SALT, _PASSWORD_ITERATIONS)


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def _membership_dict(session: Session, membership: OrganizationMembership) -> dict[str, Any]:
    user = session.get(User, membership.user_id)
    return {
        "id": membership.id,
        "user_id": membership.user_id,
        "organization_id": membership.organization_id,
        "role": membership.role,
        "status": membership.status,
        "created_at": membership.created_at,
        "updated_at": membership.updated_at,
        "email": user.email if user else None,
        "display_name": user.display_name if user else None,
    }


def list_user_memberships(session: Session, user_id: str) -> list[OrganizationMembership]:
    return list(
        session.scalars(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.status == "active",
            )
            .order_by(OrganizationMembership.created_at)
        )
    )


def membership_payloads(session: Session, user_id: str) -> list[dict[str, Any]]:
    return [_membership_dict(session, item) for item in list_user_memberships(session, user_id)]


def _principal(
    user: User, membership: OrganizationMembership, auth_session: AuthSession
) -> PrincipalContext:
    return PrincipalContext(
        user_id=user.id,
        session_id=auth_session.id,
        email=user.email,
        display_name=user.display_name,
        organization_id=membership.organization_id,
        membership_id=membership.id,
        role=membership.role,
    )


def _new_session(
    session: Session,
    user: User,
    membership: OrganizationMembership,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[str, AuthSession]:
    raw_token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = now_utc()
    record = AuthSession(
        user_id=user.id,
        membership_id=membership.id,
        organization_id=membership.organization_id,
        token_digest=_token_digest(raw_token),
        expires_at=now + timedelta(hours=max(settings.auth_session_hours, 1)),
        last_seen_at=now,
        user_agent=(user_agent or "")[:500] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    session.add(record)
    session.flush()
    return raw_token, record


def _token_response(
    session: Session,
    raw_token: str,
    user: User,
    membership: OrganizationMembership,
    auth_session: AuthSession,
) -> SessionTokenOut:
    return SessionTokenOut.model_validate(
        {
            "access_token": raw_token,
            "expires_at": auth_session.expires_at,
            "principal": _principal(user, membership, auth_session),
            "memberships": membership_payloads(session, user.id),
        }
    )


def register(
    session: Session,
    data: RegistrationCreate,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokenOut:
    existing_users = session.scalar(select(func.count()).select_from(User)) or 0
    if not settings.auth_allow_registration and existing_users > 0:
        raise IdentityForbidden("Self-service registration is disabled")
    email = data.email.lower()
    if session.scalar(select(User.id).where(User.email_normalized == email)):
        raise IdentityConflict("An account with this email already exists")
    if session.scalar(select(Organization.id).where(Organization.slug == data.organization_slug)):
        raise IdentityConflict("An organization with this slug already exists")

    organization = Organization(name=data.organization_name, slug=data.organization_slug)
    user = User(
        email=email,
        email_normalized=email,
        display_name=data.display_name,
        password_hash=_password_hash(data.password),
    )
    session.add_all((organization, user))
    try:
        session.flush()
        membership = OrganizationMembership(
            user_id=user.id,
            organization_id=organization.id,
            role="owner",
            status="active",
        )
        session.add(membership)
        session.flush()
        raw_token, auth_session = _new_session(
            session,
            user,
            membership,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        user.last_login_at = now_utc()
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise IdentityConflict("Registration conflicts with an existing account") from exc
    return _token_response(session, raw_token, user, membership, auth_session)


def login(
    session: Session,
    data: LoginCreate,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokenOut:
    email = data.email.lower()
    user = session.scalar(select(User).where(User.email_normalized == email))
    if user is None:
        _dummy_password_check(data.password)
        raise IdentityUnauthorized("Invalid email or password")
    now = now_utc()
    if user.status != "active":
        raise IdentityUnauthorized("Account is disabled")
    if user.locked_until and _aware(user.locked_until) > now:
        raise IdentityError("Account is temporarily locked", status_code=429)
    if not _verify_password(data.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= max(settings.auth_max_failed_logins, 1):
            user.locked_until = now + timedelta(minutes=max(settings.auth_lockout_minutes, 1))
            user.failed_login_count = 0
        session.commit()
        raise IdentityUnauthorized("Invalid email or password")

    memberships = list_user_memberships(session, user.id)
    if data.organization_id:
        membership = next(
            (item for item in memberships if item.organization_id == data.organization_id), None
        )
    else:
        membership = memberships[0] if len(memberships) == 1 else None
    if membership is None:
        if not memberships:
            raise IdentityForbidden("Account has no active organization membership")
        if not data.organization_id:
            raise IdentityError("organization_id is required for a multi-organization account")
        raise IdentityForbidden("Account is not a member of that organization")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    raw_token, auth_session = _new_session(
        session,
        user,
        membership,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.commit()
    return _token_response(session, raw_token, user, membership, auth_session)


def authenticate_token(session: Session, raw_token: str) -> PrincipalContext:
    if not raw_token.startswith(_TOKEN_PREFIX) or len(raw_token) < 32:
        raise IdentityUnauthorized()
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_digest == _token_digest(raw_token))
    )
    now = now_utc()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _aware(auth_session.expires_at) <= now
    ):
        raise IdentityUnauthorized()
    user = session.get(User, auth_session.user_id)
    membership = session.get(OrganizationMembership, auth_session.membership_id)
    if (
        user is None
        or user.status != "active"
        or membership is None
        or membership.status != "active"
        or membership.user_id != user.id
        or membership.organization_id != auth_session.organization_id
    ):
        raise IdentityUnauthorized()
    if _aware(auth_session.last_seen_at) < now - timedelta(minutes=5):
        auth_session.last_seen_at = now
        session.commit()
    return _principal(user, membership, auth_session)


def current_identity(session: Session, principal: PrincipalContext) -> dict[str, Any]:
    return {
        "principal": principal,
        "memberships": membership_payloads(session, principal.user_id),
    }


def logout(session: Session, principal: PrincipalContext) -> bool:
    auth_session = session.get(AuthSession, principal.session_id)
    if auth_session is None or auth_session.revoked_at is not None:
        return False
    auth_session.revoked_at = now_utc()
    session.commit()
    return True


def switch_organization(
    session: Session,
    principal: PrincipalContext,
    organization_id: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokenOut:
    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == principal.user_id,
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
        )
    )
    if membership is None:
        raise IdentityForbidden("Account is not a member of that organization")
    current = session.get(AuthSession, principal.session_id)
    if current and current.revoked_at is None:
        current.revoked_at = now_utc()
    user = session.get(User, principal.user_id)
    if user is None:
        raise IdentityUnauthorized()
    raw_token, auth_session = _new_session(
        session,
        user,
        membership,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.commit()
    return _token_response(session, raw_token, user, membership, auth_session)


def require_admin(principal: PrincipalContext, organization_id: str) -> None:
    if principal.organization_id != organization_id or principal.role not in {"owner", "admin"}:
        raise IdentityForbidden()


def list_members(
    session: Session, organization_id: str, principal: PrincipalContext
) -> list[dict[str, Any]]:
    require_admin(principal, organization_id)
    records = list(
        session.scalars(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(OrganizationMembership.created_at)
        )
    )
    return [_membership_dict(session, item) for item in records]


def add_member(
    session: Session,
    organization_id: str,
    data: MembershipCreate,
    principal: PrincipalContext,
) -> dict[str, Any]:
    require_admin(principal, organization_id)
    user = session.scalar(select(User).where(User.email_normalized == data.email.lower()))
    if user is None:
        raise IdentityError("User must register before being added", status_code=404)
    existing = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing:
        raise IdentityConflict("User already belongs to this organization")
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=organization_id,
        role=data.role,
        status="active",
        invited_by_user_id=principal.user_id,
    )
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return _membership_dict(session, membership)


def update_membership(
    session: Session,
    membership_id: str,
    data: MembershipPatch,
    principal: PrincipalContext,
) -> dict[str, Any]:
    membership = session.get(OrganizationMembership, membership_id)
    if membership is None:
        raise IdentityError("Membership not found", status_code=404)
    require_admin(principal, membership.organization_id)
    values = data.model_dump(exclude_unset=True)
    removing_owner = membership.role == "owner" and (
        values.get("role", "owner") != "owner" or values.get("status") == "suspended"
    )
    if removing_owner:
        owner_count = session.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == membership.organization_id,
                OrganizationMembership.role == "owner",
                OrganizationMembership.status == "active",
            )
        ) or 0
        if owner_count <= 1:
            raise IdentityConflict("An organization must retain at least one active owner")
    for key, value in values.items():
        setattr(membership, key, value)
    if membership.status == "suspended":
        sessions = list(
            session.scalars(
                select(AuthSession).where(
                    AuthSession.membership_id == membership.id,
                    AuthSession.revoked_at.is_(None),
                )
            )
        )
        for auth_session in sessions:
            auth_session.revoked_at = now_utc()
    session.commit()
    session.refresh(membership)
    return _membership_dict(session, membership)
