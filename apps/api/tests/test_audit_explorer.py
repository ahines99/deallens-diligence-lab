"""G43 — offline coverage for the org-level audit explorer: filters + safe CSV export."""
from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base
from src.schemas.deal_workflow import (
    ActorContext,
    DealCreate,
    FundCreate,
    OrganizationCreate,
    WorkstreamCreate,
)
from src.services import audit_explorer_service as explorer
from src.services import deal_workflow_service as workflow


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _seed_org(db: Session, suffix: str, *, actor_id: str, display_name: str):
    creator = ActorContext(actor_id=actor_id, display_name=display_name)
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"Org {suffix}", slug=f"org-{suffix}"), creator
    )
    actor = creator.model_copy(update={"organization_id": organization.id})
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), actor)
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(code=f"D-{suffix}", name=f"Project {suffix}", target_company="Target"),
        actor,
    )
    return organization, actor, fund, deal


def test_filter_by_actor_entity_and_date_narrows_results(db: Session):
    organization, actor, _fund, deal = _seed_org(
        db, "filter", actor_id="lead", display_name="Lead"
    )
    # A second actor mutates the same deal, producing an event by a different actor.
    other = actor.model_copy(update={"actor_id": "associate", "display_name": "Associate"})
    workflow.create_workstream(
        db, deal.id, WorkstreamCreate(slug="commercial", label="Commercial"), other
    )

    all_events = explorer.list_events(db, organization.id)
    assert len(all_events) >= 3  # organization.created, fund.created, deal.created, workstream.created
    assert all(event.organization_id == organization.id for event in all_events)
    # Newest first.
    timestamps = [event.created_at for event in all_events]
    assert timestamps == sorted(timestamps, reverse=True)

    # Filter by actor.
    lead_events = explorer.list_events(db, organization.id, actor="lead")
    assert lead_events and all(event.actor_id == "lead" for event in lead_events)
    associate_events = explorer.list_events(db, organization.id, actor="associate")
    assert [event.action for event in associate_events] == ["workstream.created"]

    # Filter by entity type.
    deal_events = explorer.list_events(db, organization.id, entity_type="Deal")
    assert [event.entity_id for event in deal_events] == [deal.id]

    # Filter by entity id.
    by_id = explorer.list_events(db, organization.id, entity_id=deal.id)
    assert by_id and all(event.entity_id == deal.id for event in by_id)

    # Date window: everything is after a far-past instant and before a far-future instant.
    from datetime import datetime, timezone

    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    assert len(explorer.list_events(db, organization.id, since=past, until=future)) == len(all_events)
    assert explorer.list_events(db, organization.id, since=future) == []


def test_csv_export_is_wellformed_and_formula_injection_safe(db: Session):
    # The actor display name is attacker-controlled and starts with a formula trigger.
    organization, _actor, _fund, _deal = _seed_org(
        db, "csv", actor_id="lead", display_name="=WEBSERVICE(\"//evil\")"
    )
    csv_text = explorer.export_csv(db, organization.id)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert rows, "expected at least one audit row"
    assert set(rows[0]) >= {
        "created_at",
        "actor_id",
        "actor_display_name",
        "action",
        "entity_type",
        "entity_id",
    }
    # Every rendered display name is neutralized with a leading apostrophe.
    names = {row["actor_display_name"] for row in rows}
    assert names == {"'=WEBSERVICE(\"//evil\")"}
    assert not any(row["actor_display_name"].startswith("=") for row in rows)
    # Actions are real audit actions, not corrupted by the escaping.
    assert {row["action"] for row in rows} >= {"organization.created", "deal.created"}


def test_export_and_list_are_organization_scoped(db: Session):
    org_a, _actor_a, _fund_a, _deal_a = _seed_org(db, "a", actor_id="a-lead", display_name="A")
    org_b, _actor_b, _fund_b, _deal_b = _seed_org(db, "b", actor_id="b-lead", display_name="B")

    a_events = explorer.list_events(db, org_a.id)
    assert a_events and all(event.organization_id == org_a.id for event in a_events)
    # None of org A's actors appear when scoped to org B.
    assert explorer.list_events(db, org_b.id, actor="a-lead") == []

    b_csv = explorer.export_csv(db, org_b.id)
    b_rows = list(csv.DictReader(io.StringIO(b_csv)))
    assert {row["actor_id"] for row in b_rows} == {"b-lead"}

    # A cross-org filter that matches nothing yields a header-only CSV.
    empty_csv = explorer.export_csv(db, org_b.id, actor="a-lead")
    assert list(csv.DictReader(io.StringIO(empty_csv))) == []
