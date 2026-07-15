"""Scoped API-key administration endpoints (G38). Org-admin gated."""
from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import APIRouter, HTTPException

from src.routers.deps import PrincipalDep, SessionDep
from src.schemas.api_key import ApiKeyCreate, ApiKeyCreatedOut, ApiKeyOut
from src.services import api_key_service as service
from src.services.identity_service import IdentityError

router = APIRouter(prefix="/api", tags=["api-keys"])
T = TypeVar("T")


def _call(function: Callable[..., T], *args, **kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/organizations/{organization_id}/api-keys",
    response_model=ApiKeyCreatedOut,
    status_code=201,
)
def create_api_key(
    organization_id: str,
    payload: ApiKeyCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> ApiKeyCreatedOut:
    record, plaintext = _call(
        service.create_api_key, session, organization_id, payload, principal
    )
    return ApiKeyCreatedOut(
        api_key=ApiKeyOut.model_validate(service.api_key_payload(record)),
        plaintext_key=plaintext,
    )


@router.get(
    "/organizations/{organization_id}/api-keys",
    response_model=list[ApiKeyOut],
)
def list_api_keys(
    organization_id: str, session: SessionDep, principal: PrincipalDep
) -> list[ApiKeyOut]:
    return [
        ApiKeyOut.model_validate(service.api_key_payload(record))
        for record in _call(service.list_api_keys, session, organization_id, principal)
    ]


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(
    key_id: str, session: SessionDep, principal: PrincipalDep
) -> ApiKeyOut:
    record = _call(service.revoke_api_key, session, key_id, principal)
    return ApiKeyOut.model_validate(service.api_key_payload(record))
