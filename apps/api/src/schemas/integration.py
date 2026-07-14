"""Contracts for tenant-scoped, signed outbound webhook integrations."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, HttpUrl, field_validator, model_validator

from src.schemas.common import ORMModel
from src.schemas.deal_workflow import StrictModel

WebhookStatus = Literal["active", "disabled"]
DeliveryStatus = Literal[
    "queued", "delivering", "succeeded", "failed", "dead_letter", "cancelled"
]
_EVENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


def _normalize_event_types(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        value = raw.strip()
        if value != "*" and not _EVENT_TYPE.fullmatch(value):
            raise ValueError(
                "event_types must contain '*' or dot-delimited alphanumeric event names"
            )
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("at least one event type is required")
    return normalized


class WebhookEndpointCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    url: HttpUrl
    event_types: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=200)
    secret: str = Field(min_length=16, max_length=512)

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, value: list[str]) -> list[str]:
        return _normalize_event_types(value)


class WebhookEndpointPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    url: HttpUrl | None = None
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=200)
    secret: str | None = Field(default=None, min_length=16, max_length=512)
    status: WebhookStatus | None = None

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _normalize_event_types(value)

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("update fields cannot be null")
        return self


class WebhookEndpointOut(ORMModel):
    id: str
    organization_id: str
    name: str
    url: str
    event_types: list
    secret_hint: str
    status: str
    created_by_actor_id: str | None
    created_at: datetime
    updated_at: datetime


class WebhookTestCreate(StrictModel):
    event_type: str = Field(default="integration.test", min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        normalized = _normalize_event_types([value])
        if normalized == ["*"]:
            raise ValueError("a test event requires a concrete event_type")
        return normalized[0]


class WebhookDeliveryOut(ORMModel):
    id: str
    endpoint_id: str
    organization_id: str
    audit_event_id: str | None
    replayed_from_delivery_id: str | None
    event_key: str
    event_type: str
    payload: dict
    payload_hash: str
    status: DeliveryStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    response_status: int | None
    response_body_excerpt: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class WebhookProcessResult(StrictModel):
    processed: int
    deliveries: list[WebhookDeliveryOut]


class WebhookEndpointHealth(StrictModel):
    endpoint_id: str
    endpoint_name: str
    status: str
    deliveries: int
    succeeded: int
    failed: int
    dead_letter: int
    success_rate: float | None
    average_attempts: float
    last_succeeded_at: datetime | None
    last_failed_at: datetime | None


class WebhookDeliveryHealth(StrictModel):
    organization_id: str
    window_days: int
    generated_at: datetime
    deliveries: int
    by_status: dict[str, int]
    succeeded: int
    failed: int
    dead_letter: int
    success_rate: float | None
    average_attempts: float
    last_succeeded_at: datetime | None
    last_failed_at: datetime | None
    endpoints: list[WebhookEndpointHealth]
