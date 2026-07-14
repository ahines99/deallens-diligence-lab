"""Migration regression coverage for legacy runtime-created databases."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models.deal_workflow import Deal, Fund, Organization
from src.models.workspace import Workspace


def test_legacy_linked_workspace_ownership_is_backfilled(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy-current.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "database_url", database_url)

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="Legacy Sponsor", slug="legacy-sponsor")
        session.add(organization)
        session.flush()
        fund = Fund(organization_id=organization.id, name="Legacy Fund")
        workspace = Workspace(name="Legacy Workspace", organization_id=None)
        session.add_all([fund, workspace])
        session.flush()
        deal = Deal(
            organization_id=organization.id,
            fund_id=fund.id,
            workspace_id=workspace.id,
            code="LEG-1",
            name="Legacy Deal",
            target_company="Legacy Target",
        )
        session.add(deal)
        session.commit()
        organization_id = organization.id
        workspace_id = workspace.id
    engine.dispose()

    api_root = Path(__file__).parents[1]
    config = Config(str(api_root / "alembic.ini"))
    # Alembic otherwise resolves this relative to the caller's cwd, making a root-level pytest
    # invocation behave differently from the same suite launched inside apps/api.
    config.set_main_option("script_location", str(api_root / "migrations"))
    command.stamp(config, "0fcfabe85d5e")
    command.upgrade(config, "head")
    command.check(config)

    verify_engine = create_engine(database_url)
    with Session(verify_engine) as session:
        owner_id = session.scalar(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
    verify_engine.dispose()
    assert owner_id == organization_id
