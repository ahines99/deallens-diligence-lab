"""Authentication, session, and organization-membership endpoints."""
from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import APIRouter, HTTPException, Request

from src.routers.deps import PrincipalDep, SessionDep
from src.schemas.identity import (
    CurrentIdentityOut,
    LoginCreate,
    LogoutOut,
    MembershipCreate,
    MembershipOut,
    MembershipPatch,
    OrganizationSwitch,
    RegistrationCreate,
    SessionTokenOut,
)
from src.services import identity_service as service

router = APIRouter(prefix="/api", tags=["identity"])
T = TypeVar("T")


def _call(function: Callable[..., T], *args, **kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except service.IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _client(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("User-Agent")
    ip_address = request.client.host if request.client else None
    return user_agent, ip_address


@router.post("/auth/register", response_model=SessionTokenOut, status_code=201)
def register(
    payload: RegistrationCreate, request: Request, session: SessionDep
) -> SessionTokenOut:
    user_agent, ip_address = _client(request)
    return _call(
        service.register,
        session,
        payload,
        user_agent=user_agent,
        ip_address=ip_address,
    )


@router.post("/auth/login", response_model=SessionTokenOut)
def login(payload: LoginCreate, request: Request, session: SessionDep) -> SessionTokenOut:
    user_agent, ip_address = _client(request)
    return _call(
        service.login,
        session,
        payload,
        user_agent=user_agent,
        ip_address=ip_address,
    )


@router.get("/auth/me", response_model=CurrentIdentityOut)
def me(session: SessionDep, principal: PrincipalDep) -> CurrentIdentityOut:
    return CurrentIdentityOut.model_validate(service.current_identity(session, principal))


@router.post("/auth/logout", response_model=LogoutOut)
def logout(session: SessionDep, principal: PrincipalDep) -> LogoutOut:
    return LogoutOut(revoked=service.logout(session, principal))


@router.post("/auth/switch-organization", response_model=SessionTokenOut)
def switch_organization(
    payload: OrganizationSwitch,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> SessionTokenOut:
    user_agent, ip_address = _client(request)
    return _call(
        service.switch_organization,
        session,
        principal,
        payload.organization_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )


@router.get(
    "/organizations/{organization_id}/members", response_model=list[MembershipOut]
)
def list_members(
    organization_id: str, session: SessionDep, principal: PrincipalDep
) -> list[MembershipOut]:
    return [
        MembershipOut.model_validate(item)
        for item in _call(service.list_members, session, organization_id, principal)
    ]


@router.post(
    "/organizations/{organization_id}/members",
    response_model=MembershipOut,
    status_code=201,
)
def add_member(
    organization_id: str,
    payload: MembershipCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> MembershipOut:
    item = _call(service.add_member, session, organization_id, payload, principal)
    return MembershipOut.model_validate(item)


@router.patch("/memberships/{membership_id}", response_model=MembershipOut)
def update_membership(
    membership_id: str,
    payload: MembershipPatch,
    session: SessionDep,
    principal: PrincipalDep,
) -> MembershipOut:
    item = _call(service.update_membership, session, membership_id, payload, principal)
    return MembershipOut.model_validate(item)
