"""Offline regression coverage for non-destructive diligence regeneration."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models import (
    DiligencePlan,
    DiligenceQuestion,
    Evidence,
    Memo,
    RedTeamReport,
    RiskFinding,
)
from src.models.underwriting_data import AnalysisRun, ArtifactVersion
from src.services import analysis_service


def _workspace_state(workspace_id: str) -> dict[str, tuple[str, ...]]:
    models = (
        Evidence,
        RiskFinding,
        DiligenceQuestion,
        DiligencePlan,
        Memo,
        RedTeamReport,
        AnalysisRun,
        ArtifactVersion,
    )
    with SessionLocal() as session:
        return {
            model.__tablename__: tuple(
                session.scalars(
                    select(model.id)
                    .where(model.workspace_id == workspace_id)
                    .order_by(model.id)
                )
            )
            for model in models
        }


def test_regeneration_seals_versions_and_preserves_old_evidence(client):
    workspace = client.post(
        "/api/workspaces",
        json={"name": "Versioned private deal", "deal_type": "buyout"},
    ).json()
    workspace_id = workspace["id"]
    target_response = client.post(
        f"/api/workspaces/{workspace_id}/target",
        json={
            "name": "Versioned Target",
            "target_type": "private_company",
            "revenue": 100_000_000,
            "revenue_growth": 0.08,
            "gross_margin": 0.55,
            "operating_margin": 0.12,
            "net_income": 7_000_000,
            "cash": 5_000_000,
            "total_debt": 25_000_000,
            "fiscal_year_end": "2025-12-31",
        },
    )
    assert target_response.status_code == 200, target_response.text
    assert target_response.json()["data_source"] == "User-submitted target profile (unverified)"

    first = client.post(f"/api/workspaces/{workspace_id}/risks/generate")
    assert first.status_code == 200, first.text
    first_evidence = client.get(f"/api/workspaces/{workspace_id}/evidence").json()
    assert first_evidence
    assert {item["source_type"] for item in first_evidence} == {"user_input"}
    assert all("unverified" in item["source_name"].lower() for item in first_evidence)
    assert all("SEC XBRL" not in item["evidence_text"] for item in first_evidence)
    first_refs = {item["ref"] for item in first_evidence}

    second = client.post(f"/api/workspaces/{workspace_id}/risks/generate")
    assert second.status_code == 200, second.text
    second_evidence = client.get(f"/api/workspaces/{workspace_id}/evidence").json()
    assert first_refs.issubset({item["ref"] for item in second_evidence})
    assert len(second_evidence) > len(first_evidence)

    runs = client.get(f"/api/workspaces/{workspace_id}/underwriting/analysis-runs").json()
    artifacts = client.get(
        f"/api/workspaces/{workspace_id}/underwriting/artifact-versions"
    ).json()
    assert [item["version"] for item in reversed(runs)] == [1, 2]
    assert [item["version"] for item in reversed(artifacts)] == [1, 2]
    assert artifacts[0]["analysis_run_id"] == runs[0]["id"]


def test_failed_regeneration_rolls_back_projection_evidence_and_versions(
    client, monkeypatch: pytest.MonkeyPatch
):
    workspace = client.post(
        "/api/workspaces",
        json={"name": "Atomic regeneration", "deal_type": "buyout"},
    ).json()
    workspace_id = workspace["id"]
    response = client.post(
        f"/api/workspaces/{workspace_id}/target",
        json={
            "name": "Atomic Target",
            "target_type": "private_company",
            "revenue": 80_000_000,
            "revenue_growth": 0.06,
            "gross_margin": 0.5,
            "operating_margin": 0.1,
            "net_income": 5_000_000,
            "cash": 4_000_000,
            "total_debt": 20_000_000,
            "fiscal_year_end": "2025-12-31",
        },
    )
    assert response.status_code == 200, response.text
    generated = client.post(f"/api/workspaces/{workspace_id}/risks/generate")
    assert generated.status_code == 200, generated.text
    before = _workspace_state(workspace_id)
    assert before[Evidence.__tablename__]
    assert before[Memo.__tablename__]
    assert before[ArtifactVersion.__tablename__]

    def fail_during_memo(_writer, _context):
        raise RuntimeError("synthetic memo failure")

    monkeypatch.setattr(analysis_service.ICMemoWriter, "draft", fail_during_memo)
    with SessionLocal() as session, pytest.raises(RuntimeError, match="synthetic memo failure"):
        analysis_service.run_full_analysis(session, workspace_id)

    assert _workspace_state(workspace_id) == before
