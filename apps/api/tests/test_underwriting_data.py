"""Offline tests for private-company imports, provenance, and QoE review."""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.db.base import Base, new_uuid
from src.db.session import SessionLocal, engine
from src.models.underwriting_data import SourceSnapshot  # registers all new tables
from src.models.workspace import Workspace
from src.routers.underwriting_data import router
from src.schemas.underwriting_data import (
    AccountMappingCreate,
    AnalysisRunCreate,
    ArtifactVersionCreate,
    FinancialImportCreate,
    NormalizedFinancialRow,
    PrivateTargetCreate,
    QoEAdjustmentCreate,
    QoEAdjustmentDecision,
    SourceSnapshotCreate,
)
from src.services import underwriting_data_service as service


@pytest.fixture()
def underwriting_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    workspace = Workspace(
        id=new_uuid(),
        name="Private Target Underwrite",
        deal_type="buyout",
        investment_question="Should the fund acquire this business?",
        status="draft",
    )
    session.add(workspace)
    session.commit()
    try:
        yield session, workspace
    finally:
        session.close()


def _private_target(session, workspace_id: str):
    return service.create_private_target(
        session,
        workspace_id,
        PrivateTargetCreate(
            name="Acme Industrial Software",
            sector="Vertical software",
            fiscal_year_end="12-31",
        ),
    )


def _source_payload(name: str = "Management data room") -> SourceSnapshotCreate:
    return SourceSnapshotCreate(
        source_kind="document",
        source_type="management_upload",
        source_name=name,
        filename="qoe-report.pdf",
        content_type="application/pdf",
        input_hash="a" * 64,
        content_hash="b" * 64,
        byte_size=1234,
        created_by="analyst@example.com",
    )


def _row(
    raw_account: str,
    canonical_account: str | None,
    statement: str,
    value: str,
    *,
    period_end: date = date(2025, 12, 31),
    scale: str = "1",
) -> NormalizedFinancialRow:
    return NormalizedFinancialRow(
        raw_account=raw_account,
        canonical_account=canonical_account,
        statement=statement,
        period_end=period_end,
        period_type="year" if statement != "balance_sheet" else "instant",
        value=Decimal(value),
        scale=Decimal(scale),
        source_sheet="FY25",
        source_row=10,
    )


def _xlsx_bytes(
    rows: list[list],
    *,
    headers: list[str] | None = None,
    sheet_title: str = "Management Accounts",
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_title
    worksheet.append(
        headers
        or [
            "raw_account",
            "canonical_account",
            "statement",
            "period_end",
            "period_type",
            "value",
            "scale",
            "unit",
            "currency",
        ]
    )
    for row in rows:
        worksheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def test_private_target_requires_no_ticker(underwriting_session):
    session, workspace = underwriting_session
    target = _private_target(session, workspace.id)

    assert target.name == "Acme Industrial Software"
    assert target.target_type == "private_company"
    assert target.ticker is None
    assert target.cik is None
    assert target.is_synthetic is False
    assert workspace.target_id == target.id
    assert workspace.status == "in_progress"

    with pytest.raises(service.UnderwritingDataConflict, match="already has a target"):
        _private_target(session, workspace.id)


def test_source_snapshots_are_versioned_and_immutable(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    first = service.register_source_snapshot(session, workspace.id, _source_payload())
    second = service.register_source_snapshot(session, workspace.id, _source_payload())

    assert first.version == 1
    assert second.version == 2
    assert second.supersedes_id == first.id
    assert second.input_hash == "a" * 64
    assert second.content_hash == "b" * 64
    assert first.source_kind == "user_input"
    assert first.source_type == "user_registered_reference"
    assert first.status == "partial"
    assert first.source_metadata["verification_status"] == "unverified"
    assert first.source_metadata["declared"]["source_kind"] == "document"

    second.status = "failed"
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()

    session.delete(second)
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()


def test_financial_import_maps_scales_and_reconciles(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    for raw, canonical, statement in (
        ("Adjusted EBITDA", "ebitda", "income_statement"),
        ("Total Assets", "total_assets", "balance_sheet"),
        ("Total L + E", "total_liabilities_and_equity", "balance_sheet"),
    ):
        service.create_account_mapping(
            session,
            workspace.id,
            AccountMappingCreate(
                source_type="management_financials",
                raw_account=raw,
                canonical_account=canonical,
                statement=statement,
                created_by="associate@example.com",
            ),
        )

    result = service.import_financial_rows(
        session,
        workspace.id,
        FinancialImportCreate(
            source_name="FY25 management accounts",
            rows=[
                _row("Adjusted EBITDA", None, "income_statement", "12.5", scale="1000000"),
                _row("Total Assets", None, "balance_sheet", "100"),
                _row("Total L + E", None, "balance_sheet", "100"),
            ],
        ),
    )

    assert result["snapshot"].status == "ready"
    assert result["mapped_count"] == 3
    assert result["open_exception_count"] == 0
    assert result["reconciliations"][0].status == "passed"
    facts = service.list_financial_facts(
        session, workspace.id, canonical_account="ebitda"
    )
    assert len(facts) == 1
    assert facts[0].value == Decimal("12500000")
    assert facts[0].source_locator == "FY25!row:10"
    assert facts[0].provenance["mapping_version"] == 1


def test_financial_import_preview_reports_issues_without_writing(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    payload = FinancialImportCreate(
        source_name="Preview only",
        rows=[
            _row("Total Assets", "total_assets", "balance_sheet", "100"),
            _row("Unclassified Costs", None, "income_statement", "5"),
        ],
    )

    preview = service.preview_financial_rows(session, workspace.id, payload)

    assert preview["will_write"] is False
    assert preview["proposed_source_version"] == 1
    assert preview["row_count"] == 2
    assert preview["mapped_count"] == 1
    assert preview["unmapped_count"] == 1
    assert preview["projected_status"] == "partial"
    assert {item["code"] for item in preview["exceptions"]} == {
        "unmapped_account",
        "reconciliation_incomplete",
    }
    assert service.list_source_snapshots(session, workspace.id) == []
    assert service.list_financial_facts(session, workspace.id) == []
    assert service.list_import_exceptions(session, workspace.id) == []


def test_import_records_unmapped_and_reconciliation_exceptions(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    result = service.import_financial_rows(
        session,
        workspace.id,
        FinancialImportCreate(
            source_name="Unreviewed trial balance",
            rows=[
                _row("Total Assets", "total_assets", "balance_sheet", "100"),
                _row(
                    "Total Liabilities and Equity",
                    "total_liabilities_and_equity",
                    "balance_sheet",
                    "90",
                ),
                _row("Unclassified Costs", None, "income_statement", "5"),
            ],
        ),
    )

    assert result["snapshot"].status == "partial"
    assert result["unmapped_count"] == 1
    codes = {
        item.code for item in service.list_import_exceptions(session, workspace.id)
    }
    assert codes == {"unmapped_account", "balance_sheet_imbalance"}
    reconciliation = result["reconciliations"][0]
    assert reconciliation.status == "failed"
    assert reconciliation.difference == Decimal("10")

    exception = service.list_import_exceptions(session, workspace.id)[0]
    resolved = service.resolve_import_exception(
        session,
        workspace.id,
        exception.id,
        resolved_by="vp@example.com",
    )
    assert resolved.state == "resolved"
    with pytest.raises(service.UnderwritingDataConflict, match="already resolved"):
        service.resolve_import_exception(
            session,
            workspace.id,
            exception.id,
            resolved_by="vp@example.com",
        )


def test_qoe_bridge_only_includes_approved_adjustments(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    result = service.import_financial_rows(
        session,
        workspace.id,
        FinancialImportCreate(
            source_name="QoE base",
            rows=[_row("Reported EBITDA", "ebitda", "income_statement", "20")],
        ),
    )
    snapshot = result["snapshot"]

    management = service.create_qoe_adjustment(
        session,
        workspace.id,
        QoEAdjustmentCreate(
            period_end=date(2025, 12, 31),
            bridge_layer="management",
            title="Owner compensation normalization",
            amount=Decimal("2"),
            source_snapshot_id=snapshot.id,
            source_locator="QoE!B14",
            evidence_ref="EV-101",
            created_by="associate@example.com",
        ),
    )
    sponsor = service.create_qoe_adjustment(
        session,
        workspace.id,
        QoEAdjustmentCreate(
            period_end=date(2025, 12, 31),
            bridge_layer="sponsor",
            title="Unsupported synergy",
            amount=Decimal("-1"),
            evidence_ref="EV-102",
            created_by="associate@example.com",
        ),
    )
    service.create_qoe_adjustment(
        session,
        workspace.id,
        QoEAdjustmentCreate(
            period_end=date(2025, 12, 31),
            bridge_layer="covenant",
            title="Proposed covenant add-back",
            amount=Decimal("3"),
        ),
    )
    with pytest.raises(service.UnderwritingDataConflict, match="proposer"):
        service.decide_qoe_adjustment(
            session,
            workspace.id,
            management.id,
            QoEAdjustmentDecision(decision="approve", decided_by="associate@example.com"),
        )
    service.decide_qoe_adjustment(
        session,
        workspace.id,
        management.id,
        QoEAdjustmentDecision(decision="approve", decided_by="vp@example.com"),
    )
    service.decide_qoe_adjustment(
        session,
        workspace.id,
        sponsor.id,
        QoEAdjustmentDecision(decision="approve", decided_by="vp@example.com"),
    )

    bridge = service.get_qoe_bridge(
        session,
        workspace.id,
        period_end=date(2025, 12, 31),
        source_snapshot_id=snapshot.id,
    )
    assert bridge["reported_ebitda"] == Decimal("20")
    assert bridge["management_ebitda"] == Decimal("22")
    assert bridge["sponsor_ebitda"] == Decimal("21")
    assert bridge["covenant_ebitda"] == Decimal("21")
    assert bridge["excluded_adjustment_count"] == 1
    assert set(bridge["included_adjustment_ids"]) == {management.id, sponsor.id}

    with pytest.raises(service.UnderwritingDataConflict, match="Duplicate QoE"):
        service.create_qoe_adjustment(
            session,
            workspace.id,
            QoEAdjustmentCreate(
                period_end=date(2025, 12, 31),
                bridge_layer="management",
                title="Owner compensation normalization",
                amount=Decimal("2"),
                source_snapshot_id=snapshot.id,
                source_locator="QoE!B14",
                evidence_ref="EV-101",
                created_by="associate@example.com",
            ),
        )


def test_csv_parser_preserves_lineage_and_financial_notation():
    content = (
        "account,canonical_account,statement,period_end,period_type,value,scale,currency\n"
        "Revenue,revenue,income statement,2025-12-31,annual,12.5,millions,USD\n"
        "Returns,returns,income statement,2025-12-31,annual,(250),ones,USD\n"
    ).encode()
    rows = service.parse_financial_csv(content, "management.csv")

    assert rows[0].value == Decimal("12.5")
    assert rows[0].scale == Decimal("1000000")
    assert rows[0].period_type == "year"
    assert rows[0].source_locator == "management.csv:row:2"
    assert rows[1].value == Decimal("-250")


def test_xlsx_parser_uses_normalized_first_sheet_and_cell_provenance():
    content = _xlsx_bytes(
        [
            [
                "Reported EBITDA",
                "ebitda",
                "income statement",
                date(2025, 12, 31),
                "annual",
                12.5,
                "millions",
                "currency",
                "USD",
            ]
        ]
    )
    rows = service.parse_financial_xlsx(content, "management-accounts.xlsx")

    assert len(rows) == 1
    assert rows[0].value == Decimal("12.5")
    assert rows[0].scale == Decimal("1000000")
    assert rows[0].period_end == date(2025, 12, 31)
    assert rows[0].source_sheet == "Management Accounts"
    assert rows[0].source_locator == "'Management Accounts'!F2"
    assert rows[0].provenance["cells"]["raw_account"] == "'Management Accounts'!A2"
    assert rows[0].provenance["template_version"] == "normalized-financials-v1"


def test_xlsx_parser_rejects_formulas_and_non_template_headers():
    formula = _xlsx_bytes(
        [
            [
                "Reported EBITDA",
                "ebitda",
                "income_statement",
                date(2025, 12, 31),
                "year",
                "=10+2",
                1,
                "currency",
                "USD",
            ]
        ]
    )
    with pytest.raises(service.UnderwritingDataError, match="formulas are not allowed"):
        service.parse_financial_xlsx(formula)

    wrong_headers = _xlsx_bytes(
        [["Reported EBITDA", date(2025, 12, 31), "year", 12, "do not ingest"]],
        headers=["raw_account", "period_end", "period_type", "value", "notes"],
    )
    with pytest.raises(service.UnderwritingDataError, match="unsupported header 'notes'"):
        service.parse_financial_xlsx(wrong_headers)


def test_xlsx_parser_rejects_unsafe_archive_members():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/workbook.xml", "<workbook />")
        archive.writestr("../escape.xml", "unsafe")
    with pytest.raises(service.UnderwritingDataError, match="unsafe archive path"):
        service.parse_financial_xlsx(stream.getvalue())


def test_analysis_and_artifact_versions_are_reproducible(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    source = service.register_source_snapshot(session, workspace.id, _source_payload())
    run_one = service.create_analysis_run(
        session,
        workspace.id,
        AnalysisRunCreate(
            run_type="operating_case",
            source_snapshot_ids=[source.id],
            input_manifest={"case": "base", "revenue_growth": 0.1},
            output_summary={"year_5_ebitda": 42},
            code_version="abc123",
            created_by="model@example.com",
        ),
    )
    run_two = service.create_analysis_run(
        session,
        workspace.id,
        AnalysisRunCreate(
            run_type="operating_case",
            source_snapshot_ids=[source.id],
            input_manifest={"case": "downside", "revenue_growth": -0.05},
            output_summary={"year_5_ebitda": 30},
        ),
    )
    assert run_one.version == 1
    assert run_two.version == 2
    assert run_two.supersedes_id == run_one.id
    assert run_one.input_hash == service.content_hash(
        {
            "input_manifest": {"case": "base", "revenue_growth": 0.1},
            "source_snapshot_ids": [source.id],
            "model_version": None,
            "prompt_version": None,
            "code_version": "abc123",
        }
    )
    with pytest.raises(service.UnderwritingDataError, match="input_hash"):
        service.create_analysis_run(
            session,
            workspace.id,
            AnalysisRunCreate(
                run_type="operating_case",
                input_manifest={"case": "tampered"},
                output_summary={"year_5_ebitda": 999},
                input_hash="f" * 64,
            ),
        )

    artifact_one = service.create_artifact_version(
        session,
        workspace.id,
        ArtifactVersionCreate(
            artifact_type="ic_packet",
            analysis_run_id=run_one.id,
            source_snapshot_ids=[source.id],
            input_manifest={"run_id": run_one.id},
            content_json={"recommendation": "proceed"},
        ),
    )
    artifact_two = service.create_artifact_version(
        session,
        workspace.id,
        ArtifactVersionCreate(
            artifact_type="ic_packet",
            analysis_run_id=run_two.id,
            source_snapshot_ids=[source.id],
            input_manifest={"run_id": run_two.id},
            content_text="Revised downside case",
        ),
    )
    assert artifact_two.version == 2
    assert artifact_two.supersedes_id == artifact_one.id
    assert len(artifact_one.content_hash) == 64
    with pytest.raises(service.UnderwritingDataError, match="content_hash"):
        service.create_artifact_version(
            session,
            workspace.id,
            ArtifactVersionCreate(
                artifact_type="ic_packet",
                content_json={"recommendation": "decline"},
                content_hash="f" * 64,
            ),
        )


def test_version_allocation_retries_when_a_concurrent_writer_wins(
    underwriting_session, monkeypatch
):
    """Regression: SELECT max(version)+1 then INSERT surfaced a concurrent writer's win as a raw
    IntegrityError (HTTP 500). The unique constraint is the concurrency authority; the loser must
    re-read and retry inside a savepoint — the evidence_service pattern."""
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    first = service.create_analysis_run(
        session,
        workspace.id,
        AnalysisRunCreate(
            run_type="operating_case",
            input_manifest={"case": "base"},
            output_summary={"ok": True},
        ),
    )
    assert first.version == 1

    real_allocator = service._next_stream_version
    calls = {"count": 0}

    def stale_once(inner_session, model, workspace_id, type_column, type_value):
        calls["count"] += 1
        if calls["count"] == 1:
            # Simulate having read max(version) before a concurrent writer committed version 1.
            return 1, None
        return real_allocator(inner_session, model, workspace_id, type_column, type_value)

    monkeypatch.setattr(service, "_next_stream_version", stale_once)
    second = service.create_analysis_run(
        session,
        workspace.id,
        AnalysisRunCreate(
            run_type="operating_case",
            input_manifest={"case": "downside"},
            output_summary={"ok": True},
        ),
    )
    assert calls["count"] >= 2  # the losing allocation was retried, not surfaced as a 500
    assert second.version == 2
    assert second.supersedes_id == first.id


def test_underwriting_http_contract(underwriting_session):
    session, workspace = underwriting_session
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        target_response = client.post(
            f"/api/workspaces/{workspace.id}/underwriting/private-target",
            json={"name": "HTTP PrivateCo", "sector": "Business services"},
        )
        assert target_response.status_code == 201, target_response.text
        assert target_response.json()["ticker"] is None

        csv_content = (
            "account,canonical_account,statement,period_end,period_type,value,currency\n"
            "Reported EBITDA,ebitda,income_statement,2025-12-31,year,25,USD\n"
        ).encode()
        response = client.post(
            f"/api/workspaces/{workspace.id}/underwriting/financial-imports/csv",
            files={"file": ("financials.csv", csv_content, "text/csv")},
            data={"source_name": "HTTP import", "created_by": "http@example.com"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["snapshot"]["input_hash"] == hashlib.sha256(csv_content).hexdigest()
        assert body["snapshot"]["status"] == "ready"

        facts = client.get(
            f"/api/workspaces/{workspace.id}/underwriting/financial-facts",
            params={"canonical_account": "ebitda"},
        )
        assert facts.status_code == 200
        assert len(facts.json()) == 1


def test_xlsx_upload_http_contract_and_filename_guard(underwriting_session):
    _session, workspace = underwriting_session
    app = FastAPI()
    app.include_router(router)
    content = _xlsx_bytes(
        [
            [
                "Reported EBITDA",
                "ebitda",
                "income_statement",
                date(2025, 12, 31),
                "year",
                25,
                1,
                "currency",
                "USD",
            ]
        ],
        sheet_title="QoE",
    )
    with TestClient(app) as client:
        target_response = client.post(
            f"/api/workspaces/{workspace.id}/underwriting/private-target",
            json={"name": "XLSX PrivateCo", "sector": "Business services"},
        )
        assert target_response.status_code == 201, target_response.text

        response = client.post(
            f"/api/workspaces/{workspace.id}/underwriting/financial-imports/xlsx",
            files={
                "file": (
                    "management.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"source_name": "Management workbook", "created_by": "xlsx@example.com"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["snapshot"]["input_hash"] == hashlib.sha256(content).hexdigest()
        assert body["snapshot"]["content_type"].endswith("spreadsheetml.sheet")
        assert (
            body["snapshot"]["source_metadata"]["declared_metadata"]["adapter"]
            == "normalized_xlsx"
        )

        facts = client.get(
            f"/api/workspaces/{workspace.id}/underwriting/financial-facts",
            params={"canonical_account": "ebitda"},
        ).json()
        assert facts[0]["source_locator"] == "QoE!F2"
        assert facts[0]["provenance"]["declared_metadata"]["cells"]["value"] == "QoE!F2"
        assert facts[0]["provenance"]["provenance_origin"] == (
            "user_submitted_financial_import"
        )

        unsafe_name = client.post(
            f"/api/workspaces/{workspace.id}/underwriting/financial-imports/xlsx",
            files={
                "file": (
                    "../management.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert unsafe_name.status_code == 415


def test_qoe_bridge_does_not_treat_missing_base_as_zero(underwriting_session):
    session, workspace = underwriting_session
    _private_target(session, workspace.id)
    bridge = service.get_qoe_bridge(
        session, workspace.id, period_end=date(2025, 12, 31)
    )
    assert bridge["status"] == "incomplete"
    assert bridge["reported_ebitda"] is None
    assert bridge["covenant_ebitda"] is None
    assert bridge["warnings"]


def test_source_hash_validator_rejects_non_sha256():
    with pytest.raises(ValueError, match="64-character"):
        SourceSnapshotCreate(
            source_kind="document",
            source_type="upload",
            source_name="Bad hash",
            content_hash="not-a-hash",
        )


def test_file_artifact_requires_content_hash():
    with pytest.raises(ValueError, match="content_hash"):
        ArtifactVersionCreate(artifact_type="model_export", file_uri="s3://bucket/model.xlsx")


def test_source_snapshot_model_registered():
    assert SourceSnapshot.__tablename__ == "source_snapshots"
