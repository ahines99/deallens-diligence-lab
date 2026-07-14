"""Security, outbox, signature, retry, and tenant coverage for Wave 3 webhooks."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models.integration import WebhookDelivery
from src.schemas.deal_workflow import ActorContext, DealCreate, FundCreate, OrganizationCreate
from src.schemas.integration import WebhookEndpointCreate, WebhookTestCreate
from src.services import deal_workflow_service as workflow
from src.services import webhook_service as webhooks


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class RecordingClient:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, *, content, headers):
        self.requests.append({"url": url, "content": content, "headers": headers})
        return self.responses.pop(0)


@pytest.fixture()
def webhook_config(monkeypatch):
    monkeypatch.setattr(settings, "webhook_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "webhook_allow_insecure_http", True)


@pytest.fixture()
def db(webhook_config):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _organization(db: Session, suffix: str = "one"):
    creator = ActorContext(actor_id=f"lead-{suffix}", display_name="Deal Lead")
    organization = workflow.create_organization(
        db,
        OrganizationCreate(name=f"Webhook Org {suffix}", slug=f"webhook-org-{suffix}"),
        creator,
    )
    actor = creator.model_copy(update={"organization_id": organization.id})
    return actor, organization


def _endpoint(db: Session, actor: ActorContext, organization_id: str, *, events=None):
    return webhooks.create_endpoint(
        db,
        organization_id,
        WebhookEndpointCreate(
            name="Portfolio event bus",
            url="http://localhost:9876/hooks/deallens",
            event_types=events or ["*"],
            secret="correct-horse-battery-staple",
        ),
        actor,
    )


def test_secret_is_encrypted_and_success_signature_covers_exact_body(db: Session):
    actor, organization = _organization(db, "signature")
    endpoint = _endpoint(db, actor, organization.id)

    assert "correct-horse" not in endpoint.secret_ciphertext
    assert endpoint.secret_hint == "aple"

    delivery = webhooks.queue_test_delivery(
        db,
        endpoint.id,
        WebhookTestCreate(event_type="integration.test", payload={"case": "Project Atlas"}),
        actor,
    )
    client = RecordingClient(FakeResponse(204, "accepted"))
    sent = webhooks.deliver(db, delivery.id, actor, http_client=client)

    assert sent.status == "succeeded"
    assert sent.attempt_count == 1
    assert sent.response_status == 204
    request = client.requests[0]
    assert json.loads(request["content"])["data"]["payload"] == {"case": "Project Atlas"}
    timestamp = request["headers"]["X-DealLens-Timestamp"]
    expected = hmac.new(
        b"correct-horse-battery-staple",
        timestamp.encode() + b"." + request["content"],
        hashlib.sha256,
    ).hexdigest()
    assert request["headers"]["X-DealLens-Signature"] == f"sha256={expected}"
    assert request["headers"]["X-DealLens-Delivery"] == delivery.id


def test_workflow_audit_events_fan_out_transactionally_with_filters(db: Session):
    actor, organization = _organization(db, "outbox")
    endpoint = _endpoint(db, actor, organization.id, events=["deal.created"])
    assert db.scalar(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint.id)) is None

    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), actor)
    workflow.create_deal(
        db,
        fund.id,
        DealCreate(code="ATLAS", name="Project Atlas", target_company="Atlas Software"),
        actor,
    )
    deliveries = list(
        db.scalars(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint.id))
    )
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "deal.created"
    assert deliveries[0].audit_event_id
    assert deliveries[0].payload["data"]["organization_id"] == organization.id


def test_failures_retry_then_dead_letter_and_response_is_bounded(db: Session):
    actor, organization = _organization(db, "retry")
    endpoint = _endpoint(db, actor, organization.id)
    delivery = webhooks.queue_test_delivery(
        db, endpoint.id, WebhookTestCreate(payload={"retry": True}), actor
    )
    delivery.max_attempts = 2
    db.commit()
    client = RecordingClient(FakeResponse(500, "x" * 2_000), FakeResponse(503, "unavailable"))

    first = webhooks.deliver(db, delivery.id, actor, http_client=client, force=True)
    assert first.status == "failed"
    assert first.attempt_count == 1
    assert first.next_attempt_at is not None
    assert len(first.response_body_excerpt) == 1_000

    second = webhooks.deliver(db, delivery.id, actor, http_client=client, force=True)
    assert second.status == "dead_letter"
    assert second.attempt_count == 2
    assert second.next_attempt_at is None
    assert second.response_status == 503


def test_dead_letter_replay_preserves_original_and_health_metrics(db: Session):
    actor, organization = _organization(db, "replay")
    endpoint = _endpoint(db, actor, organization.id, events=["integration.test"])
    delivery = webhooks.queue_test_delivery(
        db, endpoint.id, WebhookTestCreate(payload={"replay": True}), actor
    )
    delivery.max_attempts = 1
    db.commit()
    original = webhooks.deliver(
        db,
        delivery.id,
        actor,
        http_client=RecordingClient(FakeResponse(500, "failed")),
        force=True,
    )
    assert original.status == "dead_letter"

    health = webhooks.delivery_health(db, organization.id, actor, window_days=30)
    assert health["deliveries"] == 1
    assert health["dead_letter"] == 1
    assert health["success_rate"] == 0.0
    assert health["endpoints"][0]["dead_letter"] == 1

    replay = webhooks.replay_dead_letter(db, original.id, actor)
    assert replay.status == "queued"
    assert replay.replayed_from_delivery_id == original.id
    assert replay.payload_hash == original.payload_hash
    assert replay.attempt_count == 0
    db.refresh(original)
    assert original.status == "dead_letter"

    replayed = webhooks.deliver(
        db,
        replay.id,
        actor,
        http_client=RecordingClient(FakeResponse(204, "ok")),
        force=True,
    )
    assert replayed.status == "succeeded"
    health = webhooks.delivery_health(db, organization.id, actor, window_days=30)
    assert health["deliveries"] == 2
    assert health["succeeded"] == 1
    assert health["dead_letter"] == 1
    assert health["success_rate"] == 50.0
    with pytest.raises(webhooks.WebhookConflict, match="Only dead-letter"):
        webhooks.replay_dead_letter(db, replayed.id, actor)


def test_payload_tampering_is_dead_lettered_without_network_call(db: Session):
    actor, organization = _organization(db, "tamper")
    endpoint = _endpoint(db, actor, organization.id)
    delivery = webhooks.queue_test_delivery(db, endpoint.id, WebhookTestCreate(), actor)
    delivery.payload = {**delivery.payload, "data": {"tampered": True}}
    db.commit()
    client = RecordingClient(FakeResponse(200))

    result = webhooks.deliver(db, delivery.id, actor, http_client=client)
    assert result.status == "dead_letter"
    assert result.error_message == "Stored webhook payload hash mismatch"
    assert client.requests == []


def test_atomic_claim_prevents_duplicate_send_between_workers(db: Session):
    actor, organization = _organization(db, "claim")
    endpoint = _endpoint(db, actor, organization.id)
    delivery = webhooks.queue_test_delivery(db, endpoint.id, WebhookTestCreate(), actor)
    second_worker = Session(db.get_bind(), expire_on_commit=False)
    try:
        stale_delivery = second_worker.get(WebhookDelivery, delivery.id)
        assert stale_delivery.status == "queued"
        primary_client = RecordingClient(FakeResponse(200, "ok"))
        assert webhooks.deliver(
            db, delivery.id, actor, http_client=primary_client
        ).status == "succeeded"

        duplicate_client = RecordingClient(FakeResponse(200, "duplicate"))
        with pytest.raises(webhooks.WebhookConflict, match="claimed by another worker"):
            webhooks.deliver(
                second_worker,
                delivery.id,
                actor,
                http_client=duplicate_client,
                force=True,
            )
        assert duplicate_client.requests == []
    finally:
        second_worker.close()


def test_webhook_tenant_scope_is_non_enumerable_at_service_boundary(db: Session):
    actor, organization = _organization(db, "tenant")
    endpoint = _endpoint(db, actor, organization.id)
    outsider = ActorContext(actor_id="outsider", organization_id="f" * 32)

    with pytest.raises(webhooks.WebhookForbidden):
        webhooks.list_endpoints(db, organization.id, outsider)
    with pytest.raises(webhooks.WebhookForbidden):
        webhooks.queue_test_delivery(db, endpoint.id, WebhookTestCreate(), outsider)


def test_authenticated_webhook_administration_requires_trusted_role(
    db: Session, monkeypatch
):
    actor, organization = _organization(db, "role")
    monkeypatch.setattr(settings, "auth_required", True)
    with pytest.raises(webhooks.WebhookError, match="administrator role required"):
        _endpoint(db, actor, organization.id)

    administrator = actor.model_copy(update={"roles": ("integration_admin",)})
    assert _endpoint(db, administrator, organization.id).organization_id == organization.id


def test_versioned_webhook_api_never_returns_ciphertext(client, webhook_config):
    created_org = client.post(
        "/api/v1/organizations",
        json={"name": "Versioned Hooks", "slug": "versioned-hooks"},
        headers={"X-Actor-ID": "integration-admin"},
    )
    assert created_org.status_code == 201, created_org.text
    organization_id = created_org.json()["id"]
    headers = {
        "X-Actor-ID": "integration-admin",
        "X-Organization-ID": organization_id,
    }
    response = client.post(
        f"/api/v1/organizations/{organization_id}/webhooks",
        json={
            "name": "v1 receiver",
            "url": "http://localhost:9876/hook",
            "event_types": ["*"],
            "secret": "never-return-this-secret",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.headers["X-DealLens-API-Version"] == "1"
    body = response.json()
    assert body["secret_hint"] == "cret"
    assert "secret" not in body
    assert "ciphertext" not in response.text
