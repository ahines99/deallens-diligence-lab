"""H1 regression: /api/sec/ingest is body-addressed, so it must enforce the caller's tenant
boundary itself (the workspace-path middleware never sees it). Validates the org-scoped lookup
that backs the endpoint, including ownership via a linked deal."""
from __future__ import annotations

import pytest

from src.db.base import new_uuid
from src.db.session import SessionLocal
from src.models import Workspace
from src.models.deal_workflow import Deal, Fund, Organization
from src.services.common import NotFound, get_workspace_scoped_or_404


@pytest.fixture()
def two_orgs(client):
    # Unique slugs per test: the client's SQLite DB is session-scoped and persists across tests.
    suffix = new_uuid()[:8]
    with SessionLocal() as session:
        org_a = Organization(name="Org A", slug=f"scope-org-a-{suffix}")
        org_b = Organization(name="Org B", slug=f"scope-org-b-{suffix}")
        session.add_all([org_a, org_b])
        session.flush()
        directly_owned = Workspace(name="A direct", organization_id=org_a.id)
        deal_linked = Workspace(name="A via deal", organization_id=None)
        session.add_all([directly_owned, deal_linked])
        session.flush()
        fund = Fund(organization_id=org_a.id, name="Fund")
        session.add(fund)
        session.flush()
        session.add(
            Deal(
                organization_id=org_a.id,
                fund_id=fund.id,
                workspace_id=deal_linked.id,
                code="SCOPE-1",
                name="Scope deal",
                target_company="Target",
            )
        )
        session.commit()
        yield {
            "org_a": org_a.id,
            "org_b": org_b.id,
            "direct_ws": directly_owned.id,
            "deal_ws": deal_linked.id,
        }


def test_directly_owned_workspace_is_org_scoped(two_orgs):
    with SessionLocal() as session:
        # Owner org resolves it; another org gets a 404-equivalent, no existence oracle.
        assert get_workspace_scoped_or_404(session, two_orgs["direct_ws"], two_orgs["org_a"])
        with pytest.raises(NotFound):
            get_workspace_scoped_or_404(session, two_orgs["direct_ws"], two_orgs["org_b"])


def test_deal_linked_workspace_ownership_is_honored(two_orgs):
    with SessionLocal() as session:
        # Ownership flows through the linked deal even when organization_id is null.
        assert get_workspace_scoped_or_404(session, two_orgs["deal_ws"], two_orgs["org_a"])
        with pytest.raises(NotFound):
            get_workspace_scoped_or_404(session, two_orgs["deal_ws"], two_orgs["org_b"])


def test_no_caller_org_degrades_to_plain_lookup(two_orgs):
    # Auth-off dev mode (principal is None) matches the middleware: no cross-tenant check.
    with SessionLocal() as session:
        assert get_workspace_scoped_or_404(session, two_orgs["direct_ws"], None)
        with pytest.raises(NotFound):
            get_workspace_scoped_or_404(session, "does-not-exist", two_orgs["org_a"])
