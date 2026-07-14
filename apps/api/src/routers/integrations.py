"""Versioned, tenant-scoped API surface for signed outbound webhooks."""
from __future__ import annotations

from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, HTTPException, Query

from src.routers.deal_workflow import ActorDep
from src.routers.deps import SessionDep
from src.schemas.integration import (
    DeliveryStatus,
    WebhookDeliveryOut,
    WebhookDeliveryHealth,
    WebhookEndpointCreate,
    WebhookEndpointOut,
    WebhookEndpointPatch,
    WebhookProcessResult,
    WebhookTestCreate,
)
from src.services import webhook_service as service

router = APIRouter(prefix="/api", tags=["integrations"])
T = TypeVar("T")


def _call(function: Callable[..., T], *args, **kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except service.WebhookError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/organizations/{organization_id}/webhooks",
    response_model=WebhookEndpointOut,
    status_code=201,
)
def create_webhook(
    organization_id: str,
    payload: WebhookEndpointCreate,
    session: SessionDep,
    actor: ActorDep,
) -> WebhookEndpointOut:
    endpoint = _call(service.create_endpoint, session, organization_id, payload, actor)
    return WebhookEndpointOut.model_validate(endpoint)


@router.get(
    "/organizations/{organization_id}/webhooks", response_model=list[WebhookEndpointOut]
)
def list_webhooks(
    organization_id: str, session: SessionDep, actor: ActorDep
) -> list[WebhookEndpointOut]:
    return [
        WebhookEndpointOut.model_validate(item)
        for item in _call(service.list_endpoints, session, organization_id, actor)
    ]


@router.patch("/webhooks/{endpoint_id}", response_model=WebhookEndpointOut)
def update_webhook(
    endpoint_id: str,
    payload: WebhookEndpointPatch,
    session: SessionDep,
    actor: ActorDep,
) -> WebhookEndpointOut:
    endpoint = _call(service.update_endpoint, session, endpoint_id, payload, actor)
    return WebhookEndpointOut.model_validate(endpoint)


@router.post(
    "/webhooks/{endpoint_id}/test", response_model=WebhookDeliveryOut, status_code=202
)
def queue_webhook_test(
    endpoint_id: str,
    payload: WebhookTestCreate,
    session: SessionDep,
    actor: ActorDep,
) -> WebhookDeliveryOut:
    delivery = _call(service.queue_test_delivery, session, endpoint_id, payload, actor)
    return WebhookDeliveryOut.model_validate(delivery)


@router.get(
    "/organizations/{organization_id}/webhook-deliveries",
    response_model=list[WebhookDeliveryOut],
)
def list_webhook_deliveries(
    organization_id: str,
    session: SessionDep,
    actor: ActorDep,
    status: DeliveryStatus | None = None,
    endpoint_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
) -> list[WebhookDeliveryOut]:
    return [
        WebhookDeliveryOut.model_validate(item)
        for item in _call(
            service.list_deliveries,
            session,
            organization_id,
            actor,
            status=status,
            endpoint_id=endpoint_id,
            limit=limit,
        )
    ]


@router.get(
    "/organizations/{organization_id}/webhook-deliveries/health",
    response_model=WebhookDeliveryHealth,
)
def webhook_delivery_health(
    organization_id: str,
    session: SessionDep,
    actor: ActorDep,
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> WebhookDeliveryHealth:
    result = _call(
        service.delivery_health,
        session,
        organization_id,
        actor,
        window_days=window_days,
    )
    return WebhookDeliveryHealth.model_validate(result)


@router.post(
    "/webhook-deliveries/{delivery_id}/replay",
    response_model=WebhookDeliveryOut,
    status_code=202,
)
def replay_webhook_delivery(
    delivery_id: str, session: SessionDep, actor: ActorDep
) -> WebhookDeliveryOut:
    delivery = _call(service.replay_dead_letter, session, delivery_id, actor)
    return WebhookDeliveryOut.model_validate(delivery)


@router.post("/webhook-deliveries/{delivery_id}/send", response_model=WebhookDeliveryOut)
def send_webhook_delivery(
    delivery_id: str, session: SessionDep, actor: ActorDep
) -> WebhookDeliveryOut:
    delivery = _call(service.deliver, session, delivery_id, actor, force=True)
    return WebhookDeliveryOut.model_validate(delivery)


@router.post(
    "/organizations/{organization_id}/webhook-deliveries/process",
    response_model=WebhookProcessResult,
)
def process_webhook_deliveries(
    organization_id: str,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> WebhookProcessResult:
    deliveries = _call(
        service.process_pending,
        session,
        organization_id,
        actor,
        limit=limit,
    )
    return WebhookProcessResult(
        processed=len(deliveries),
        deliveries=[WebhookDeliveryOut.model_validate(item) for item in deliveries],
    )
