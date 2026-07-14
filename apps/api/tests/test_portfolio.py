"""Portfolio command-center aggregation, filtering, export, and health coverage."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.db.session import SessionLocal
from src.models.deal_workflow import (
    ConditionToClose,
    Deal,
    DealLedgerEntry,
    DealStageGate,
    DealTask,
    DealWorkstream,
    DiligenceRequest,
    Fund,
    ICDecision,
    ICPacket,
    Organization,
)
from src.models.target import Target
from src.models.underwriting_data import (
    CanonicalFinancialFact,
    FinancialImportException,
    FinancialReconciliation,
    QoEAdjustment,
    SourceSnapshot,
)
from src.models.underwriting_model import UnderwritingCaseVersion
from src.models.workspace import Workspace

AS_OF = date(2026, 7, 13)


@pytest.fixture()
def portfolio_records(client):
    del client  # ensures the application lifespan has created every table
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as session:
        organization = Organization(name=f"Portfolio {suffix}", slug=f"portfolio-{suffix}")
        session.add(organization)
        session.flush()
        buyout = Fund(
            organization_id=organization.id,
            name="Fund V",
            strategy="buyout",
        )
        growth = Fund(
            organization_id=organization.id,
            name="Growth II",
            strategy="growth_equity",
        )
        session.add_all((buyout, growth))
        session.flush()

        workspace = Workspace(
            name="Atlas Underwrite",
            organization_id=organization.id,
            deal_type="buyout",
            investment_question="Acquire Atlas?",
        )
        session.add(workspace)
        session.flush()
        target = Target(
            workspace_id=workspace.id,
            name="Atlas Software",
            target_type="private_company",
            sector="Vertical Software",
            data_source="User-submitted target profile (unverified)",
        )
        session.add(target)
        session.flush()
        workspace.target_id = target.id

        atlas = Deal(
            organization_id=organization.id,
            fund_id=buyout.id,
            workspace_id=workspace.id,
            code="ATL-101",
            name="Project Atlas",
            target_company="Atlas Software",
            stage="ic_review",
            status="active",
            owner_actor_id="lead@example.test",
            ic_date=AS_OF + timedelta(days=5),
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        borealis = Deal(
            organization_id=organization.id,
            fund_id=growth.id,
            code="BOR-202",
            name="Project Borealis",
            target_company="Borealis Health",
            stage="screening",
            status="active",
            ic_date=AS_OF + timedelta(days=60),
        )
        session.add_all((atlas, borealis))
        session.flush()

        session.add_all(
            (
                DealStageGate(
                    deal_id=atlas.id,
                    stage="ic_review",
                    code="model",
                    label="Model approved",
                    required=True,
                    status="satisfied",
                ),
                DealStageGate(
                    deal_id=atlas.id,
                    stage="ic_review",
                    code="legal",
                    label="Legal complete",
                    required=True,
                    status="pending",
                ),
                DealTask(
                    deal_id=atlas.id,
                    title="Resolve churn cohort",
                    status="blocked",
                    priority="critical",
                    assignee_actor_id="associate@example.test",
                    due_date=AS_OF - timedelta(days=4),
                ),
                DealTask(
                    deal_id=atlas.id,
                    title="Complete market work",
                    status="complete",
                    priority="high",
                    assignee_actor_id="associate@example.test",
                    due_date=AS_OF - timedelta(days=1),
                ),
                DealWorkstream(
                    deal_id=atlas.id,
                    slug="commercial",
                    label="Commercial",
                    status="blocked",
                    lead_actor_id="principal@example.test",
                    due_date=AS_OF - timedelta(days=2),
                ),
                DiligenceRequest(
                    deal_id=atlas.id,
                    request_number=1,
                    title="Customer cohorts",
                    question="Provide monthly cohorts",
                    status="requested",
                    priority="critical",
                    owner_actor_id="associate@example.test",
                    due_date=AS_OF - timedelta(days=3),
                    requested_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ),
                DealLedgerEntry(
                    deal_id=atlas.id,
                    root_entry_id=None,
                    entry_type="risk",
                    title="Customer concentration",
                    description="Top customer is material",
                    status="open",
                    severity="critical",
                    owner_actor_id="principal@example.test",
                    evidence_refs=["EV-001"],
                    related_artifact_ids=[],
                ),
            )
        )

        packet = ICPacket(
            deal_id=atlas.id,
            version=1,
            title="Atlas IC Packet",
            status="approved",
            ready_for_submission=True,
            content_hash=("a" * 63) + suffix[0],
        )
        session.add(packet)
        session.flush()
        decision = ICDecision(
            packet_id=packet.id,
            sequence=1,
            decision="conditional",
            rationale="Close the customer work",
            decided_by_actor_id="partner@example.test",
        )
        session.add(decision)
        session.flush()
        session.add(
            ConditionToClose(
                deal_id=atlas.id,
                packet_id=packet.id,
                decision_id=decision.id,
                description="Validate customer reference",
                owner_actor_id="principal@example.test",
                due_date=AS_OF - timedelta(days=1),
                status="open",
            )
        )

        source = SourceSnapshot(
            workspace_id=workspace.id,
            target_id=target.id,
            source_kind="upload",
            source_type="management_financials",
            source_name="Management P&L",
            version=1,
            input_hash="1" * 64,
            content_hash="2" * 64,
            record_count=1,
            status="ready",
            created_by="associate@example.test",
            created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            sealed_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
        session.add(source)
        session.flush()
        fact = CanonicalFinancialFact(
            workspace_id=workspace.id,
            target_id=target.id,
            source_snapshot_id=source.id,
            statement="income_statement",
            raw_account="Adjusted EBITDA",
            raw_account_normalized="adjusted ebitda",
            canonical_account="ebitda",
            mapping_state="mapped",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            period_type="duration",
            raw_value=Decimal("100"),
            scale_factor=Decimal("1"),
            value=Decimal("100"),
            unit="currency",
            currency="USD",
            source_locator="Sheet1!B2",
            row_hash="3" * 64,
        )
        session.add(fact)
        session.add(
            FinancialReconciliation(
                workspace_id=workspace.id,
                source_snapshot_id=source.id,
                period_end=date(2025, 12, 31),
                assets=Decimal("500"),
                liabilities_and_equity=Decimal("500"),
                difference=Decimal("0"),
                tolerance=Decimal("1"),
                status="passed",
            )
        )
        session.add(
            FinancialImportException(
                workspace_id=workspace.id,
                source_snapshot_id=source.id,
                fact_id=fact.id,
                code="UNMAPPED_LABEL",
                severity="high",
                state="open",
                message="Review one source label",
                created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            )
        )
        session.add(
            QoEAdjustment(
                workspace_id=workspace.id,
                target_id=target.id,
                source_snapshot_id=source.id,
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                bridge_layer="sponsor",
                title="Owner compensation",
                category="owner_compensation",
                amount=Decimal("10"),
                status="approved",
                dedupe_key="4" * 64,
            )
        )
        session.add(
            UnderwritingCaseVersion(
                workspace_id=workspace.id,
                case_key="downside",
                label="Downside",
                version=1,
                assumptions={},
                result={
                    "returns": {"moic": 1.2, "xirr": 0.10},
                    "summary": {
                        "minimum_liquidity": -5,
                        "first_covenant_breach": "Y2",
                        "first_debt_service_default": None,
                    },
                },
                input_hash="5" * 64,
                output_hash="6" * 64,
                created_by="associate@example.test",
            )
        )
        session.commit()
        yield {
            "organization_id": organization.id,
            "atlas_id": atlas.id,
            "atlas_workspace_id": workspace.id,
            "buyout_fund_id": buyout.id,
        }


def test_portfolio_dashboard_explains_pipeline_execution_and_financial_quality(
    client, portfolio_records
):
    response = client.get(
        f"/api/organizations/{portfolio_records['organization_id']}/portfolio",
        params={"as_of": AS_OF.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["headline"] == {
        "deals": 2,
        "active_deals": 2,
        "funds": 2,
        "at_ic": 1,
        "ic_next_30_days": 1,
        "overdue_tasks": 1,
        "critical_risks": 1,
        "open_conditions": 1,
        "average_readiness": 21.2,
    }
    assert {item["key"]: item["count"] for item in body["strategy_exposure"]} == {
        "buyout": 1,
        "growth_equity": 1,
    }
    stage_funnel = {item["key"]: item for item in body["stage_funnel"]}
    assert stage_funnel["ic_review"] == {
        "key": "ic_review",
        "label": "Ic Review",
        "count": 1,
        "percent": 50.0,
    }
    assert stage_funnel["screening"]["count"] == 1
    assert sum(item["count"] for item in body["stage_funnel"]) == 2
    assert {item["key"]: (item["count"], item["percent"]) for item in body["sector_exposure"]} == {
        "Unclassified": (1, 50.0),
        "Vertical Software": (1, 50.0),
    }
    assert body["upcoming_ic"][0]["days_until"] == 5
    assert body["overdue_tasks"][0]["days_overdue"] == 4
    atlas_workstreams = next(
        item for item in body["workstream_health"] if item["deal_code"] == "ATL-101"
    )
    assert atlas_workstreams["health"] == "blocked"
    assert body["diligence_sla"][0]["sla_status"] == "overdue"
    assert body["critical_risks"][0]["evidence_refs"] == ["EV-001"]
    assert body["conditions_to_close"][0]["days_overdue"] == 1
    assert body["team_workload"][0]["actor_id"] == "associate@example.test"

    atlas = next(item for item in body["deals"] if item["code"] == "ATL-101")
    assert atlas["readiness_score"] == 42.5
    assert atlas["source_health"]["status"] == "ready"
    assert atlas["financial_quality"]["mapping_coverage"] == 100.0
    assert atlas["financial_quality"]["reconciliation_score"] == 100.0
    assert atlas["financial_quality"]["reported_ebitda"] == 100.0
    assert atlas["financial_quality"]["sponsor_adjusted_ebitda"] == 110.0
    assert atlas["financial_quality"]["qoe_materiality"] == 0.1
    assert body["downside_watchlist"]
    assert body["covenant_watchlist"][0]["value"] == "Y2"
    assert body["import_exceptions"][0]["age_days"] == 11


def test_portfolio_search_stage_fund_export_health_and_tenant_scope(client, portfolio_records):
    base = f"/api/organizations/{portfolio_records['organization_id']}/portfolio"
    assert client.get(base, params={"search": "Borealis"}).json()["headline"]["deals"] == 1
    assert client.get(base, params={"stage": "ic_review"}).json()["headline"]["deals"] == 1
    filtered = client.get(base, params={"fund_id": portfolio_records["buyout_fund_id"]}).json()
    assert [item["code"] for item in filtered["deals"]] == ["ATL-101"]

    export = client.get(f"{base}/export.csv")
    assert export.status_code == 200
    assert "attachment;" in export.headers["content-disposition"]
    assert "ATL-101,Project Atlas" in export.text
    health = client.get(f"{base}/health")
    assert health.status_code == 200
    assert health.json()["sources"]["ready"] == 1
    assert health.json()["workspaces_without_sources"] == 1

    suffix = uuid.uuid4().hex[:8]
    account = client.post(
        "/api/auth/register",
        json={
            "email": f"outsider-{suffix}@example.test",
            "display_name": "Outsider",
            "password": "correct horse portfolio battery",
            "organization_name": f"Outsider {suffix}",
            "organization_slug": f"outsider-{suffix}",
        },
    ).json()
    assert (
        client.get(
            base,
            headers={"Authorization": f"Bearer {account['access_token']}"},
        ).status_code
        == 404
    )


def test_unified_activity_timeline_filters_cross_plane_events(client, portfolio_records):
    base = f"/api/organizations/{portfolio_records['organization_id']}/activity"
    response = client.get(base, params={"deal_id": portfolio_records["atlas_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    event_types = {item["event_type"] for item in body["items"]}
    assert "source.sealed" in event_types
    assert "case.version.created" in event_types
    assert all(item["deal_id"] == portfolio_records["atlas_id"] for item in body["items"])

    data_only = client.get(
        base,
        params={"deal_id": portfolio_records["atlas_id"], "category": "data"},
    ).json()
    assert data_only["total"] == 1
    assert data_only["items"][0]["source"] == "source_snapshot"
    assert data_only["items"][0]["detail"]["content_hash"] == "2" * 64
