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
    MembershipPermissionsOut,
    OIDCLoginOut,
    OrganizationSwitch,
    PermissionGrantPatch,
    RegistrationCreate,
    SessionTokenOut,
)
from src.services import (
    demo_service,
    identity_service as service,
    oidc_service,
    permission_service,
)

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


@router.post("/auth/demo", response_model=SessionTokenOut, status_code=201)
def start_demo_session(request: Request, session: SessionDep) -> SessionTokenOut:
    """One-click guest session inside the shared demo organization (DEMO_MODE only)."""
    user_agent, ip_address = _client(request)
    return _call(
        demo_service.start_guest_session,
        session,
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


@router.get("/auth/oidc/login", response_model=OIDCLoginOut)
def oidc_login() -> OIDCLoginOut:
    """Return the IdP authorize URL + state (G48). 404 when OIDC_ENABLED=false."""
    authorize_url, state = _call(oidc_service.build_authorize_url)
    return OIDCLoginOut(authorize_url=authorize_url, state=state)


@router.get("/auth/oidc/callback", response_model=SessionTokenOut)
def oidc_callback(
    code: str, request: Request, session: SessionDep, state: str | None = None
) -> SessionTokenOut:
    """Complete the OIDC code exchange and issue a DealLens session (G48)."""
    user_agent, ip_address = _client(request)
    return _call(
        oidc_service.handle_callback,
        session,
        code,
        state,
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


@router.get(
    "/memberships/{membership_id}/permissions", response_model=MembershipPermissionsOut
)
def get_membership_permissions(
    membership_id: str, session: SessionDep, principal: PrincipalDep
) -> MembershipPermissionsOut:
    """Role defaults, explicit grants/revokes, and the effective capability set (G49)."""
    return MembershipPermissionsOut.model_validate(
        _call(permission_service.list_membership_permissions, session, membership_id, principal)
    )


@router.put(
    "/memberships/{membership_id}/permissions", response_model=MembershipPermissionsOut
)
def set_membership_permission(
    membership_id: str,
    payload: PermissionGrantPatch,
    session: SessionDep,
    principal: PrincipalDep,
) -> MembershipPermissionsOut:
    """Grant or revoke one capability for a membership (owners/admins only; G49)."""
    return MembershipPermissionsOut.model_validate(
        _call(
            permission_service.set_membership_permission,
            session,
            membership_id,
            payload,
            principal,
        )
    )
