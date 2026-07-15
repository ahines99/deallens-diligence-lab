"""Read-only tokenized workspace share links (G44).

Management endpoints are workspace-/org-scoped (tenant-guarded by the ``/api/workspaces/{id}``
middleware and the service's ownership checks) and require an authenticated principal. The public
read endpoint ``GET /api/shared/{token}`` needs no session: the opaque token is itself the
authorization. Its path prefix is registered as public in ``main.py`` so the session-required guard
lets it through, and the token is resolved here. A revoked or expired token is 410; unknown is 404.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import OptionalPrincipalDep, PrincipalDep, SessionDep
from src.schemas.share_link import (
    ShareLinkCreate,
    ShareLinkCreatedOut,
    ShareLinkOut,
    SharedWorkspaceSnapshot,
)
from src.services import share_link_service as service
from src.services.common import NotFound
from src.services.identity_service import IdentityError

router = APIRouter(prefix="/api", tags=["share-links"])


@router.post(
    "/workspaces/{workspace_id}/share-links",
    response_model=ShareLinkCreatedOut,
    status_code=201,
)
def create_share_link(
    workspace_id: str,
    payload: ShareLinkCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> ShareLinkCreatedOut:
    try:
        record, token = service.create_share_link(session, workspace_id, payload, principal)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return ShareLinkCreatedOut(share_link=ShareLinkOut.model_validate(record), token=token)


@router.get(
    "/workspaces/{workspace_id}/share-links",
    response_model=list[ShareLinkOut],
)
def list_share_links(
    workspace_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> list[ShareLinkOut]:
    try:
        records = service.list_share_links(session, workspace_id, principal)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return [ShareLinkOut.model_validate(record) for record in records]


@router.post("/share-links/{share_link_id}/revoke", response_model=ShareLinkOut)
def revoke_share_link(
    share_link_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> ShareLinkOut:
    try:
        record = service.revoke_share_link(session, share_link_id, principal)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return ShareLinkOut.model_validate(record)


@router.get("/shared/{token}", response_model=SharedWorkspaceSnapshot)
def read_shared_snapshot(
    token: str,
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> SharedWorkspaceSnapshot:
    """Public, session-less read of a workspace's non-confidential snapshot via a share token."""
    try:
        share_link = service.resolve_share_link(session, token)
        snapshot = service.build_snapshot(session, share_link)
    except NotFound as exc:
        # ShareLinkGone (revoked/expired) carries status_code=410; plain NotFound stays 404.
        raise HTTPException(status_code=getattr(exc, "status_code", 404), detail=exc.message) from exc
    return SharedWorkspaceSnapshot.model_validate(snapshot)


__all__ = ["router"]
