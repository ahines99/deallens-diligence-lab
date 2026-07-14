"""Encrypted endpoint registry and durable, HMAC-signed webhook outbox."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import new_uuid, now_utc
from src.models.deal_workflow import Organization, WorkflowAuditEvent
from src.models.integration import WebhookDelivery, WebhookEndpoint
from src.schemas.deal_workflow import ActorContext
from src.schemas.integration import (
    WebhookEndpointCreate,
    WebhookEndpointPatch,
    WebhookTestCreate,
)

_MAX_RESPONSE_EXCERPT = 1_000
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 3_600
_STALE_DELIVERY_MINUTES = 10
_ADMIN_ROLES = {"integration_admin", "organization_admin"}


class WebhookError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class WebhookConflict(WebhookError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


class WebhookForbidden(WebhookError):
    def __init__(self) -> None:
        super().__init__("Organization scope does not permit this operation", status_code=403)


class WebhookNotFound(WebhookError):
    def __init__(self, resource: str) -> None:
        super().__init__(f"{resource} not found", status_code=404)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _fernet() -> Fernet:
    if not settings.webhook_encryption_key:
        raise WebhookError(
            "WEBHOOK_ENCRYPTION_KEY must be configured before webhook endpoints are used",
            status_code=503,
        )
    try:
        return Fernet(settings.webhook_encryption_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise WebhookError("WEBHOOK_ENCRYPTION_KEY is not a valid Fernet key", status_code=503) from exc


def _encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def _decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise WebhookError(
            "Webhook signing secret cannot be decrypted with the configured key", status_code=503
        ) from exc


def _verify_org_scope(actor: ActorContext | None, organization_id: str) -> None:
    if actor and actor.organization_id and actor.organization_id != organization_id:
        raise WebhookForbidden()


def _require_integration_admin(actor: ActorContext | None) -> None:
    # Internal workers have no request actor. Authenticated HTTP requests must carry a trusted role
    # claim from the SSO proxy; local unauthenticated development keeps its zero-config posture.
    if actor is not None and settings.auth_required and not (_ADMIN_ROLES & set(actor.roles)):
        raise WebhookError("Integration administrator role required", status_code=403)


def _organization(
    session: Session, organization_id: str, actor: ActorContext | None = None
) -> Organization:
    _require_integration_admin(actor)
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise WebhookNotFound("Organization")
    _verify_org_scope(actor, organization_id)
    return organization


def _endpoint(
    session: Session, endpoint_id: str, actor: ActorContext | None = None
) -> WebhookEndpoint:
    _require_integration_admin(actor)
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise WebhookNotFound("Webhook endpoint")
    _verify_org_scope(actor, endpoint.organization_id)
    return endpoint


def _delivery(
    session: Session, delivery_id: str, actor: ActorContext | None = None
) -> WebhookDelivery:
    _require_integration_admin(actor)
    delivery = session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise WebhookNotFound("Webhook delivery")
    _verify_org_scope(actor, delivery.organization_id)
    return delivery


def _validate_url(value: str) -> str:
    url = str(value)
    parsed = urlsplit(url)
    allowed_schemes = {"https"}
    if settings.webhook_allow_insecure_http:
        allowed_schemes.add("http")
    if parsed.scheme.lower() not in allowed_schemes:
        raise WebhookError("Webhook URLs must use HTTPS")
    if not parsed.hostname:
        raise WebhookError("Webhook URL requires a hostname")
    if parsed.username or parsed.password:
        raise WebhookError("Webhook URLs cannot contain credentials")
    if parsed.fragment:
        raise WebhookError("Webhook URLs cannot contain fragments")
    try:
        parsed.port
    except ValueError as exc:
        raise WebhookError("Webhook URL contains an invalid port") from exc
    if not settings.webhook_allow_insecure_http:
        _reject_non_public_host(parsed.hostname, resolve_dns=False)
    return url


def _reject_non_public_host(hostname: str, *, resolve_dns: bool) -> None:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        raise WebhookError("Webhook destinations cannot target localhost")

    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(normalized)))
    except ValueError:
        if resolve_dns:
            try:
                addresses.update(
                    item[4][0] for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
                )
            except OSError as exc:
                raise WebhookError("Webhook destination hostname could not be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise WebhookError("Webhook destinations must resolve only to public IP addresses")


def _assert_delivery_destination(url: str) -> None:
    normalized = _validate_url(url)
    if not settings.webhook_allow_insecure_http:
        hostname = urlsplit(normalized).hostname
        if hostname:
            _reject_non_public_host(hostname, resolve_dns=True)


def _commit(session: Session, entity: Any) -> Any:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise WebhookConflict("The webhook operation conflicts with an existing record") from exc
    session.refresh(entity)
    return entity


def _append_audit(
    session: Session,
    endpoint: WebhookEndpoint,
    actor: ActorContext | None,
    action: str,
    detail: dict[str, Any],
) -> WorkflowAuditEvent:
    event = WorkflowAuditEvent(
        organization_id=endpoint.organization_id,
        deal_id=None,
        actor_id=actor.actor_id if actor else None,
        actor_display_name=actor.display_name if actor else None,
        action=action,
        entity_type=type(endpoint).__name__,
        entity_id=endpoint.id,
        detail=detail,
        request_id=actor.request_id if actor else None,
    )
    session.add(event)
    session.flush()
    queue_for_audit_event(session, event)
    return event


def create_endpoint(
    session: Session,
    organization_id: str,
    data: WebhookEndpointCreate,
    actor: ActorContext | None = None,
) -> WebhookEndpoint:
    _organization(session, organization_id, actor)
    url = _validate_url(str(data.url))
    endpoint = WebhookEndpoint(
        organization_id=organization_id,
        name=data.name,
        url=url,
        event_types=data.event_types,
        secret_ciphertext=_encrypt_secret(data.secret),
        secret_hint=data.secret[-4:],
        status="active",
        created_by_actor_id=actor.actor_id if actor else None,
    )
    session.add(endpoint)
    session.flush()
    _append_audit(
        session,
        endpoint,
        actor,
        "webhook.endpoint.created",
        {"name": endpoint.name, "url": endpoint.url, "event_types": endpoint.event_types},
    )
    return _commit(session, endpoint)


def list_endpoints(
    session: Session, organization_id: str, actor: ActorContext | None = None
) -> list[WebhookEndpoint]:
    _organization(session, organization_id, actor)
    return list(
        session.scalars(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.organization_id == organization_id)
            .order_by(WebhookEndpoint.created_at.desc())
        )
    )


def update_endpoint(
    session: Session,
    endpoint_id: str,
    data: WebhookEndpointPatch,
    actor: ActorContext | None = None,
) -> WebhookEndpoint:
    endpoint = _endpoint(session, endpoint_id, actor)
    changed: list[str] = []
    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        endpoint.name = values["name"]
        changed.append("name")
    if "url" in values:
        endpoint.url = _validate_url(str(values["url"]))
        changed.append("url")
    if "event_types" in values:
        endpoint.event_types = values["event_types"]
        changed.append("event_types")
    if "status" in values:
        endpoint.status = values["status"]
        changed.append("status")
    if "secret" in values:
        endpoint.secret_ciphertext = _encrypt_secret(values["secret"])
        endpoint.secret_hint = values["secret"][-4:]
        changed.append("secret")
    session.flush()
    _append_audit(
        session,
        endpoint,
        actor,
        "webhook.endpoint.updated",
        {"changed_fields": changed, "status": endpoint.status},
    )
    return _commit(session, endpoint)


def _matches(endpoint: WebhookEndpoint, event_type: str) -> bool:
    return "*" in endpoint.event_types or event_type in endpoint.event_types


def _event_payload(event: WorkflowAuditEvent) -> dict[str, Any]:
    return {
        "specversion": "1.0",
        "id": event.id,
        "type": event.action,
        "source": f"/organizations/{event.organization_id}",
        "subject": f"{event.entity_type}/{event.entity_id}",
        "time": event.created_at.isoformat(),
        "data": {
            "organization_id": event.organization_id,
            "deal_id": event.deal_id,
            "actor_id": event.actor_id,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "detail": event.detail,
            "request_id": event.request_id,
        },
    }


def queue_for_audit_event(
    session: Session, event: WorkflowAuditEvent
) -> list[WebhookDelivery]:
    """Fan an append-only audit event into the transactional webhook outbox."""
    endpoints = list(
        session.scalars(
            select(WebhookEndpoint).where(
                WebhookEndpoint.organization_id == event.organization_id,
                WebhookEndpoint.status == "active",
            )
        )
    )
    payload = _event_payload(event)
    payload_hash = _payload_hash(payload)
    queued: list[WebhookDelivery] = []
    for endpoint in endpoints:
        if not _matches(endpoint, event.action):
            continue
        existing = session.scalar(
            select(WebhookDelivery.id).where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.event_key == event.id,
            )
        )
        if existing:
            continue
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            organization_id=event.organization_id,
            audit_event_id=event.id,
            event_key=event.id,
            event_type=event.action,
            payload=payload,
            payload_hash=payload_hash,
            status="queued",
            next_attempt_at=now_utc(),
        )
        session.add(delivery)
        queued.append(delivery)
    return queued


def queue_test_delivery(
    session: Session,
    endpoint_id: str,
    data: WebhookTestCreate,
    actor: ActorContext | None = None,
) -> WebhookDelivery:
    endpoint = _endpoint(session, endpoint_id, actor)
    if endpoint.status != "active":
        raise WebhookConflict("Disabled webhook endpoints cannot receive test deliveries")
    event_id = new_uuid()
    occurred_at = now_utc()
    payload = {
        "specversion": "1.0",
        "id": event_id,
        "type": data.event_type,
        "source": f"/organizations/{endpoint.organization_id}",
        "subject": f"WebhookEndpoint/{endpoint.id}",
        "time": occurred_at.isoformat(),
        "data": {
            "organization_id": endpoint.organization_id,
            "actor_id": actor.actor_id if actor else None,
            "test": True,
            "payload": data.payload,
        },
    }
    delivery = WebhookDelivery(
        endpoint_id=endpoint.id,
        organization_id=endpoint.organization_id,
        audit_event_id=None,
        event_key=f"test:{event_id}",
        event_type=data.event_type,
        payload=payload,
        payload_hash=_payload_hash(payload),
        status="queued",
        next_attempt_at=occurred_at,
    )
    session.add(delivery)
    return _commit(session, delivery)


def list_deliveries(
    session: Session,
    organization_id: str,
    actor: ActorContext | None = None,
    *,
    status: str | None = None,
    endpoint_id: str | None = None,
    limit: int = 200,
) -> list[WebhookDelivery]:
    _organization(session, organization_id, actor)
    statement = select(WebhookDelivery).where(
        WebhookDelivery.organization_id == organization_id
    )
    if status:
        statement = statement.where(WebhookDelivery.status == status)
    if endpoint_id:
        endpoint = _endpoint(session, endpoint_id, actor)
        if endpoint.organization_id != organization_id:
            raise WebhookForbidden()
        statement = statement.where(WebhookDelivery.endpoint_id == endpoint_id)
    return list(
        session.scalars(statement.order_by(WebhookDelivery.created_at.desc()).limit(limit))
    )


def replay_dead_letter(
    session: Session,
    delivery_id: str,
    actor: ActorContext | None = None,
) -> WebhookDelivery:
    """Queue a traceable retry while preserving the terminal original delivery."""
    original = _delivery(session, delivery_id, actor)
    endpoint = _endpoint(session, original.endpoint_id, actor)
    if original.status != "dead_letter":
        raise WebhookConflict("Only dead-letter webhook deliveries can be replayed")
    if endpoint.status != "active":
        raise WebhookConflict("The webhook endpoint must be active before replay")
    if not hmac.compare_digest(_payload_hash(original.payload), original.payload_hash):
        raise WebhookConflict("The dead-letter payload no longer matches its stored hash")

    replay = WebhookDelivery(
        endpoint_id=original.endpoint_id,
        organization_id=original.organization_id,
        audit_event_id=original.audit_event_id,
        replayed_from_delivery_id=original.id,
        event_key=f"replay:{original.id}:{new_uuid()}",
        event_type=original.event_type,
        payload=original.payload,
        payload_hash=original.payload_hash,
        status="queued",
        attempt_count=0,
        max_attempts=original.max_attempts,
        next_attempt_at=now_utc(),
    )
    session.add(replay)
    return _commit(session, replay)


def delivery_health(
    session: Session,
    organization_id: str,
    actor: ActorContext | None = None,
    *,
    window_days: int = 30,
) -> dict[str, Any]:
    """Aggregate delivery outcomes without exposing encrypted endpoint secrets."""
    _organization(session, organization_id, actor)
    since = now_utc() - timedelta(days=window_days)
    deliveries = list(
        session.scalars(
            select(WebhookDelivery).where(
                WebhookDelivery.organization_id == organization_id,
                WebhookDelivery.created_at >= since,
            )
        )
    )
    endpoints = list(
        session.scalars(
            select(WebhookEndpoint).where(WebhookEndpoint.organization_id == organization_id)
        )
    )

    def summarize(records: list[WebhookDelivery]) -> dict[str, Any]:
        statuses = {
            status: sum(item.status == status for item in records)
            for status in ("queued", "delivering", "succeeded", "failed", "dead_letter", "cancelled")
        }
        completed = statuses["succeeded"] + statuses["dead_letter"] + statuses["cancelled"]
        last_succeeded = max(
            (item.delivered_at for item in records if item.status == "succeeded" and item.delivered_at),
            default=None,
        )
        last_failed = max(
            (
                item.last_attempt_at
                for item in records
                if item.status in {"failed", "dead_letter"} and item.last_attempt_at
            ),
            default=None,
        )
        return {
            "deliveries": len(records),
            "by_status": statuses,
            "succeeded": statuses["succeeded"],
            "failed": statuses["failed"],
            "dead_letter": statuses["dead_letter"],
            "success_rate": (
                round(statuses["succeeded"] / completed * 100, 1) if completed else None
            ),
            "average_attempts": (
                round(sum(item.attempt_count for item in records) / len(records), 2)
                if records
                else 0.0
            ),
            "last_succeeded_at": last_succeeded,
            "last_failed_at": last_failed,
        }

    overall = summarize(deliveries)
    endpoint_rows = []
    for endpoint in endpoints:
        summary = summarize([item for item in deliveries if item.endpoint_id == endpoint.id])
        endpoint_rows.append(
            {
                "endpoint_id": endpoint.id,
                "endpoint_name": endpoint.name,
                "status": endpoint.status,
                **{key: value for key, value in summary.items() if key != "by_status"},
            }
        )
    endpoint_rows.sort(key=lambda item: (-item["dead_letter"], item["endpoint_name"]))
    return {
        "organization_id": organization_id,
        "window_days": window_days,
        "generated_at": now_utc(),
        **overall,
        "endpoints": endpoint_rows,
    }


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = timestamp.encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def _response_excerpt(response: Any) -> str:
    try:
        value = response.text
    except Exception:  # pragma: no cover - defensive around third-party response objects
        return ""
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return value[:_MAX_RESPONSE_EXCERPT]


def _mark_failed(
    session: Session,
    delivery: WebhookDelivery,
    message: str,
    *,
    response_status: int | None = None,
    response_excerpt: str | None = None,
    permanent: bool = False,
) -> WebhookDelivery:
    delivery.response_status = response_status
    delivery.response_body_excerpt = response_excerpt
    delivery.error_message = message[:2_000]
    exhausted = permanent or delivery.attempt_count >= delivery.max_attempts
    if exhausted:
        delivery.status = "dead_letter"
        delivery.next_attempt_at = None
    else:
        delivery.status = "failed"
        delay = min(
            _RETRY_BASE_SECONDS * (2 ** max(delivery.attempt_count - 1, 0)),
            _RETRY_MAX_SECONDS,
        )
        delivery.next_attempt_at = now_utc() + timedelta(seconds=delay)
    return _commit(session, delivery)


def deliver(
    session: Session,
    delivery_id: str,
    actor: ActorContext | None = None,
    *,
    http_client: Any | None = None,
    force: bool = False,
) -> WebhookDelivery:
    delivery = _delivery(session, delivery_id, actor)
    endpoint = _endpoint(session, delivery.endpoint_id, actor)
    if delivery.status == "succeeded":
        raise WebhookConflict("A successful webhook delivery cannot be sent again")
    if delivery.status in {"dead_letter", "cancelled", "delivering"}:
        raise WebhookConflict(f"Webhook delivery is {delivery.status}")
    if endpoint.status != "active":
        delivery.status = "cancelled"
        delivery.next_attempt_at = None
        delivery.error_message = "Endpoint is disabled"
        return _commit(session, delivery)

    now = now_utc()
    due_at = delivery.next_attempt_at
    if due_at and due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    if not force and due_at and due_at > now:
        raise WebhookConflict("Webhook delivery is not due for retry yet")

    claim_clauses = [
        WebhookDelivery.id == delivery.id,
        WebhookDelivery.status.in_(("queued", "failed")),
    ]
    if not force:
        claim_clauses.append(
            or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now)
        )
    claimed = session.execute(
        update(WebhookDelivery)
        .where(*claim_clauses)
        .values(
            status="delivering",
            attempt_count=WebhookDelivery.attempt_count + 1,
            last_attempt_at=now,
            response_status=None,
            response_body_excerpt=None,
            error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise WebhookConflict("Webhook delivery was claimed by another worker")
    session.commit()
    session.refresh(delivery)

    body = _canonical_bytes(delivery.payload)
    if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), delivery.payload_hash):
        return _mark_failed(session, delivery, "Stored webhook payload hash mismatch", permanent=True)

    timestamp = str(int(now.timestamp()))
    try:
        _assert_delivery_destination(endpoint.url)
        secret = _decrypt_secret(endpoint.secret_ciphertext)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DealLens-Webhooks/1.0",
            "X-DealLens-Event": delivery.event_type,
            "X-DealLens-Delivery": delivery.id,
            "X-DealLens-Timestamp": timestamp,
            "X-DealLens-Signature": _signature(secret, timestamp, body),
        }
        if http_client is None:
            with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                response = client.post(endpoint.url, content=body, headers=headers)
        else:
            response = http_client.post(endpoint.url, content=body, headers=headers)
    except (httpx.HTTPError, WebhookError, OSError) as exc:
        return _mark_failed(session, delivery, str(exc))

    delivery.response_status = int(response.status_code)
    delivery.response_body_excerpt = _response_excerpt(response)
    if 200 <= response.status_code < 300:
        delivery.status = "succeeded"
        delivery.delivered_at = now_utc()
        delivery.next_attempt_at = None
        delivery.error_message = None
        return _commit(session, delivery)
    return _mark_failed(
        session,
        delivery,
        f"Webhook endpoint returned HTTP {response.status_code}",
        response_status=int(response.status_code),
        response_excerpt=delivery.response_body_excerpt,
    )


def process_pending(
    session: Session,
    organization_id: str | None = None,
    actor: ActorContext | None = None,
    *,
    limit: int = 100,
    http_client: Any | None = None,
) -> list[WebhookDelivery]:
    if organization_id:
        _organization(session, organization_id, actor)
    stale_before = now_utc() - timedelta(minutes=_STALE_DELIVERY_MINUTES)
    stale_clauses = [
        WebhookDelivery.status == "delivering",
        WebhookDelivery.last_attempt_at < stale_before,
    ]
    if organization_id:
        stale_clauses.append(WebhookDelivery.organization_id == organization_id)
    recovered = session.execute(
        update(WebhookDelivery)
        .where(*stale_clauses)
        .values(
            status="failed",
            next_attempt_at=now_utc(),
            error_message="Recovered a stale in-progress delivery",
        )
        .execution_options(synchronize_session=False)
    )
    if recovered.rowcount:
        session.commit()

    now = now_utc()
    clauses = [
        WebhookDelivery.status.in_(("queued", "failed")),
        or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now),
    ]
    if organization_id:
        clauses.append(WebhookDelivery.organization_id == organization_id)
    pending = list(
        session.scalars(
            select(WebhookDelivery)
            .where(*clauses)
            .order_by(WebhookDelivery.created_at)
            .limit(limit)
        )
    )
    results: list[WebhookDelivery] = []
    for delivery in pending:
        try:
            results.append(
                deliver(session, delivery.id, actor, http_client=http_client, force=True)
            )
        except WebhookConflict:
            # Another worker atomically claimed the same candidate after this batch was selected.
            continue
    return results
