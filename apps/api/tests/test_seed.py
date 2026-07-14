"""Tenant-ownership checks for the optional live-data demo seeder."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models import Organization, Workspace
from src.seed.load_seed import (
    SeedConfigurationError,
    _resolve_seed_organization_id,
    seed_demo,
)


@pytest.fixture()
def seed_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_seed_requires_an_owner_organization_when_authentication_is_enabled(
    seed_session, monkeypatch
):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "seed_organization_slug", "")
    with pytest.raises(SeedConfigurationError, match="Register the first owner"):
        _resolve_seed_organization_id(seed_session)


def test_seed_uses_the_only_organization(seed_session, monkeypatch):
    monkeypatch.setattr(settings, "seed_organization_slug", "")
    organization = Organization(name="Deal Team", slug="deal-team")
    seed_session.add(organization)
    seed_session.commit()

    assert _resolve_seed_organization_id(seed_session) == organization.id


def test_seed_requires_and_honors_a_slug_when_multiple_organizations_exist(
    seed_session, monkeypatch
):
    organizations = [
        Organization(name="Alpha", slug="alpha"),
        Organization(name="Beta", slug="beta"),
    ]
    seed_session.add_all(organizations)
    seed_session.commit()
    monkeypatch.setattr(settings, "seed_organization_slug", "")

    with pytest.raises(SeedConfigurationError, match="Multiple organizations"):
        _resolve_seed_organization_id(seed_session)

    monkeypatch.setattr(settings, "seed_organization_slug", "beta")
    assert _resolve_seed_organization_id(seed_session) == organizations[1].id


def test_seed_demo_persists_workspace_tenant_without_network(seed_session):
    organization = Organization(name="Owned Demo", slug="owned-demo")
    seed_session.add(organization)
    seed_session.commit()

    workspace = seed_demo(
        seed_session,
        "",
        "buyout",
        [],
        organization_id=organization.id,
    )

    persisted = seed_session.get(Workspace, workspace.id)
    assert persisted is not None
    assert persisted.organization_id == organization.id
