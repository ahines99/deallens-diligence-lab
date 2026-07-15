"""Comment-thread endpoints with @mentions on governed artifacts (G41). Org-scoped.

Posting and resolving require an authenticated principal (the author is server-derived, never
client-supplied) and are blocked for viewer memberships by the middleware read-only guard and again
in the service. Reads are tenant-scoped to the caller's organization when a principal is present.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.routers.deps import OptionalPrincipalDep, PrincipalDep, SessionDep
from src.schemas.comment import CommentCreate, CommentOut, CommentThreadOut
from src.services import comment_service as service
from src.services.common import NotFound
from src.services.identity_service import IdentityError

router = APIRouter(prefix="/api", tags=["comments"])


@router.post("/comments", response_model=CommentOut, status_code=201)
def create_comment(
    payload: CommentCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> CommentOut:
    try:
        comment = service.create_comment(session, payload, principal)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CommentOut.model_validate(comment)


@router.get("/comments", response_model=list[CommentThreadOut])
def list_comments(
    session: SessionDep,
    principal: OptionalPrincipalDep,
    entity_type: str = Query(...),
    entity_id: str = Query(...),
) -> list[CommentThreadOut]:
    organization_id = principal.organization_id if principal else None
    roots = service.list_thread(session, organization_id, entity_type, entity_id)
    return [
        CommentThreadOut.model_validate(
            {
                **CommentOut.model_validate(root).model_dump(),
                "replies": [CommentOut.model_validate(reply) for reply in getattr(root, "replies", [])],
            }
        )
        for root in roots
    ]


@router.post("/comments/{comment_id}/resolve", response_model=CommentOut)
def resolve_comment(
    comment_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> CommentOut:
    try:
        comment = service.resolve_comment(session, comment_id, principal)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return CommentOut.model_validate(comment)


__all__ = ["router"]
