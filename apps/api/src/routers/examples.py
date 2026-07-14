"""Bundled example private deal: one-click load plus downloadable import templates."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

from src.routers.deps import SessionDep
from src.services import example_deal_service
from src.services.underwriting_data_service import UnderwritingDataError

router = APIRouter(prefix="/api/examples", tags=["examples"])

_TEXT_TYPES = {".csv": "text/csv", ".txt": "text/plain"}


class ExampleDealOut(BaseModel):
    organization_id: str
    fund_id: str
    deal_id: str
    workspace_id: str
    deal_code: str
    import_status: str
    open_exceptions: int


class TemplateInfo(BaseModel):
    name: str
    description: str


@router.post("/private-deal", response_model=ExampleDealOut, status_code=201)
def load_private_deal(
    request: Request,
    session: SessionDep,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    header_actor_name: Annotated[str | None, Header(alias="X-Actor-Name")] = None,
) -> ExampleDealOut:
    principal = getattr(request.state, "principal", None)
    try:
        result = example_deal_service.load_example_deal(
            session,
            organization_id=principal.organization_id if principal else None,
            actor_id=(principal.user_id if principal else None) or header_actor_id or "demo.user",
            actor_name=(
                (principal.display_name if principal else None)
                or header_actor_name
                or "Demo user"
            ),
        )
    except (UnderwritingDataError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExampleDealOut(**result)


@router.get("/templates", response_model=list[TemplateInfo])
def list_templates() -> list[TemplateInfo]:
    return [
        TemplateInfo(name=name, description=description)
        for name, description in example_deal_service.TEMPLATE_FILES.items()
    ]


@router.get("/templates/{name}")
def download_template(name: str) -> Response:
    try:
        content = example_deal_service.read_template(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No template named '{name}'") from None
    suffix = name[name.rfind(".") :].lower()
    return Response(
        content=content,
        media_type=_TEXT_TYPES.get(suffix, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
