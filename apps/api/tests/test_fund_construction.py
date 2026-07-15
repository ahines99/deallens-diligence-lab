"""Fund-level portfolio construction: exposure, concentration limits, pacing, sizing coverage (G29).

Sizing comes only from committed sponsor equity carried on a deal's underwriting case; a deal without
that figure is UNSIZED and never imputed. Every test is offline and mirrors ``test_portfolio.py``'s
organization/fund/deal fixture shape.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from src.db.session import SessionLocal
from src.models.deal_workflow import Deal, Fund, Organization
from src.models.target import Target
from src.models.underwriting_model import UnderwritingCaseVersion
from src.models.workspace import Workspace

AS_OF = date(2026, 7, 13)


def _hash(n: int) -> str:
    return f"{n:064d}"


def _add_deal(
    session,
    *,
    organization_id: str,
    fund_id: str,
    code: str,
    sector: str,
    stage: str,
    committed: float | None,
    seq: int,
) -> None:
    """Create a deal with a workspace/target (sector) and, when sized, a base case carrying equity."""
    workspace = Workspace(
        name=f"{code} Underwrite",
        organization_id=organization_id,
        deal_type="buyout",
        investment_question=f"Acquire {code}?",
    )
    session.add(workspace)
    session.flush()
    target = Target(
        workspace_id=workspace.id,
        name=f"{code} Co",
        target_type="private_company",
        sector=sector,
        data_source="User-submitted target profile (unverified)",
    )
    session.add(target)
    session.flush()
    workspace.target_id = target.id
    session.add(
        Deal(
            organization_id=organization_id,
            fund_id=fund_id,
            workspace_id=workspace.id,
            code=code,
            name=f"Project {code}",
            target_company=f"{code} Co",
            stage=stage,
            status="active",
        )
    )
    if committed is not None:
        session.add(
            UnderwritingCaseVersion(
                workspace_id=workspace.id,
                case_key="base",
                label="Base",
                version=1,
                assumptions={},
                result={
                    "sources_uses": {"sponsor_equity": committed},
                    "returns": {},
                    "summary": {},
                },
                input_hash=_hash(seq),
                output_hash=_hash(seq + 10_000),
                created_by="associate@example.test",
            )
        )


@pytest.fixture()
def construction_records(client):
    del client  # ensures the application lifespan has created every table
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as session:
        organization = Organization(name=f"Construct {suffix}", slug=f"construct-{suffix}")
        session.add(organization)
        session.flush()
        concentrated = Fund(
            organization_id=organization.id,
            name="Concentrated V",
            vintage_year=2022,
            strategy="buyout",
        )
        diversified = Fund(
            organization_id=organization.id,
            name="Diversified II",
            vintage_year=2024,
            strategy="growth_equity",
        )
        session.add_all((concentrated, diversified))
        session.flush()

        # Concentrated fund: deployed 100. Software 80% (breaches 30% sector cap); one unsized deal.
        seq = 0
        for code, sector, committed in (
            ("CON-1", "Software", 60.0),
            ("CON-2", "Software", 20.0),
            ("CON-3", "Healthcare", 20.0),
            ("CON-4", "Industrials", None),  # no case -> unsized, excluded, never imputed
        ):
            seq += 1
            _add_deal(
                session,
                organization_id=organization.id,
                fund_id=concentrated.id,
                code=code,
                sector=sector,
                stage="diligence",
                committed=committed,
                seq=seq,
            )

        # Diversified fund: 8 deals of 10 across 4 sectors -> deployed 80, each sector 25%, each
        # deal 12.5%; nothing breaches the default 30%/15% caps.
        sectors = ("Alpha", "Alpha", "Beta", "Beta", "Gamma", "Gamma", "Delta", "Delta")
        for i, sector in enumerate(sectors):
            seq += 1
            _add_deal(
                session,
                organization_id=organization.id,
                fund_id=diversified.id,
                code=f"DIV-{i + 1}",
                sector=sector,
                stage="diligence",
                committed=10.0,
                seq=seq,
            )
        session.commit()
        yield {
            "organization_id": organization.id,
            "concentrated_fund_id": concentrated.id,
            "diversified_fund_id": diversified.id,
        }


def _fund(body: dict, fund_id: str) -> dict:
    return next(item for item in body["funds"] if item["fund_id"] == fund_id)


def test_exposure_aggregation_sums_to_sized_capital(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    body = client.get(base, params={"as_of": AS_OF.isoformat()}).json()

    concentrated = _fund(body, construction_records["concentrated_fund_id"])
    # Deployed is only the sum of SIZED deals (60 + 20 + 20); the unsized deal contributes nothing.
    assert concentrated["deployed"] == 100.0
    sector = {item["key"]: item["exposure_pct"] for item in concentrated["exposures"]["sector"]}
    assert sector == {"Software": 0.8, "Healthcare": 0.2}
    # Every dimension's buckets sum to <= 100% of sized capital (exactly 100% when fully allocated).
    for dimension in ("sector", "strategy", "stage"):
        total = sum(item["exposure_pct"] for item in concentrated["exposures"][dimension])
        assert total <= 1.0 + 1e-9
    assert sum(sector.values()) == pytest.approx(1.0)
    # Strategy is the fund mandate: a single 100% bucket.
    assert {item["key"]: item["exposure_pct"] for item in concentrated["exposures"]["strategy"]} == {
        "buyout": 1.0
    }


def test_limit_breach_detection_flags_over_concentration_with_excess(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    body = client.get(base, params={"as_of": AS_OF.isoformat()}).json()

    concentrated = _fund(body, construction_records["concentrated_fund_id"])
    sector_breaches = [
        item for item in concentrated["concentration_breaches"] if item["dimension"] == "sector"
    ]
    assert len(sector_breaches) == 1
    breach = sector_breaches[0]
    # Software is 80% against the default 30% cap: excess is exactly 0.50.
    assert (breach["key"], breach["exposure_pct"], breach["limit"]) == ("Software", 0.8, 0.3)
    assert breach["excess"] == pytest.approx(0.5)
    # Healthcare (20%) is under the cap and is not reported as a breach.
    assert "Healthcare" not in {item["key"] for item in sector_breaches}

    # The diversified fund breaches nothing: each sector 25% <= 30%, each deal 12.5% <= 15%, and its
    # single-strategy bucket is structural (not a concentration breach).
    diversified = _fund(body, construction_records["diversified_fund_id"])
    assert diversified["concentration_breaches"] == []

    # A configurable, tighter sector cap turns the diversified fund's 25% buckets into breaches.
    tighter = client.get(
        base, params={"as_of": AS_OF.isoformat(), "single_sector_max": 0.20}
    ).json()
    tight_div = _fund(tighter, construction_records["diversified_fund_id"])
    assert {item["key"] for item in tight_div["concentration_breaches"]} == {
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
    }
    assert all(item["excess"] == pytest.approx(0.05) for item in tight_div["concentration_breaches"])


def test_single_deal_and_strategy_limits(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    body = client.get(base, params={"as_of": AS_OF.isoformat()}).json()

    concentrated = _fund(body, construction_records["concentrated_fund_id"])
    deal_breaches = {
        item["key"]: item["exposure_pct"]
        for item in concentrated["concentration_breaches"]
        if item["dimension"] == "deal"
    }
    # CON-1 (60%), CON-2 (20%), CON-3 (20%) each exceed the default 15% single-deal cap.
    assert deal_breaches == {"CON-1": 0.6, "CON-2": 0.2, "CON-3": 0.2}
    # Strategy is never a per-fund breach dimension (one fund == one mandate).
    assert not any(
        item["dimension"] == "strategy" for item in concentrated["concentration_breaches"]
    )


def test_pacing_status_tracks_vintage_schedule(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    fund_id = construction_records["concentrated_fund_id"]

    # Expected pace is deterministic from the 2022 vintage and a 5-year investment period.
    expected = min((AS_OF - date(2022, 1, 1)).days / 365.25 / 5, 1.0)

    behind = _fund(
        client.get(
            base, params={"as_of": AS_OF.isoformat(), "target_fund_size": 1000}
        ).json(),
        fund_id,
    )
    # Deployed 100 of a 1000 target => 10% actual, far below the ~90% expected pace.
    assert behind["pacing"]["actual_pct"] == 0.1
    assert behind["pacing"]["expected_pct"] == pytest.approx(expected, abs=1e-3)
    assert behind["pacing"]["status"] == "behind"

    ahead = _fund(
        client.get(base, params={"as_of": AS_OF.isoformat(), "target_fund_size": 50}).json(),
        fund_id,
    )
    # Deployed 100 of a 50 target => 200% actual, well ahead of pace.
    assert ahead["pacing"]["actual_pct"] == 2.0
    assert ahead["pacing"]["status"] == "ahead"

    on_track = _fund(
        client.get(
            base,
            params={"as_of": AS_OF.isoformat(), "target_fund_size": round(100 / expected, 2)},
        ).json(),
        fund_id,
    )
    assert on_track["pacing"]["status"] == "on_track"

    # Without a target the pacing status is unknown, never guessed.
    unknown = _fund(
        client.get(base, params={"as_of": AS_OF.isoformat()}).json(), fund_id
    )
    assert unknown["pacing"]["actual_pct"] is None
    assert unknown["pacing"]["status"] == "unknown"


def test_sizing_coverage_excludes_unsized_deals_without_imputing(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    body = client.get(base, params={"as_of": AS_OF.isoformat()}).json()

    concentrated = _fund(body, construction_records["concentrated_fund_id"])
    coverage = concentrated["sizing_coverage"]
    # The Industrials deal carries no committed capital: it is excluded from deployed capital and
    # from every exposure denominator, and surfaced in coverage rather than imputed.
    assert coverage == {
        "total_deals": 4,
        "sized_deals": 3,
        "unsized_deals": 1,
        "coverage_pct": 75.0,
        "deployed": 100.0,
        "unsized_deal_codes": ["CON-4"],
    }
    # Industrials never appears in exposure because the unsized deal was excluded, not zero-imputed.
    assert "Industrials" not in {item["key"] for item in concentrated["exposures"]["sector"]}


def test_fund_construction_is_organization_scoped(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    assert client.get(base, params={"as_of": AS_OF.isoformat()}).status_code == 200

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


def test_fund_id_filter_narrows_to_a_single_fund(client, construction_records):
    base = f"/api/organizations/{construction_records['organization_id']}/fund-construction"
    body = client.get(
        base,
        params={
            "as_of": AS_OF.isoformat(),
            "fund_id": construction_records["diversified_fund_id"],
        },
    ).json()
    assert [item["fund_id"] for item in body["funds"]] == [
        construction_records["diversified_fund_id"]
    ]
