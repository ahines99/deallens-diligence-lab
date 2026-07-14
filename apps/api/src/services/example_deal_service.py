"""One-click example private deal, loaded through the same governed pipeline users drive.

The bundled inputs are clearly labeled fictional. The loader deliberately stops where human
judgment starts: QoE adjustments stay proposed, import exceptions stay open, claims stay
unreviewed, and the deal stays at its first stage — so a visitor experiences the real
approval workflow instead of finding it pre-completed.
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models import Organization
from src.models.deal_workflow import Deal, Fund
from src.schemas.deal_workflow import ActorContext, DealCreate, FundCreate, OrganizationCreate
from src.schemas.deal_intelligence import DocumentTextCreate
from src.schemas.underwriting_data import (
    FinancialImportCreate,
    PrivateTargetCreate,
    QoEAdjustmentCreate,
)
from src.schemas.workspace import WorkspaceCreate
from src.services import (
    deal_intelligence_service,
    deal_workflow_service,
    underwriting_data_service,
    workspace_service,
)

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "seed" / "example_deal"

# Files a visitor can download as templates for their own imports.
TEMPLATE_FILES = {
    "management_financials.csv": "Management financials import template (CSV)",
    "cim-extract.txt": "Example data-room document: CIM extract",
    "customer-contracts.txt": "Example data-room document: customer contract summary",
    "qoe-findings.txt": "Example data-room document: QoE preliminary findings",
}

_DEMO_ORG_NAME = "Example Capital (Demo)"
_DEMO_ORG_SLUG = "example-capital-demo"
_DEMO_FUND_NAME = "DealLens Demo Fund I"
_DEAL_CODE_PREFIX = "EXM"
_TARGET_NAME = "Meridian Compliance Software (Fictional)"
_ANALYST = "demo.analyst@example.test"

_QOE_ADJUSTMENTS = [
    dict(
        bridge_layer="management",
        title="Owner compensation normalization",
        description=(
            "Founder-CEO drew $2.3M total compensation in FY2025 against an estimated $0.9M "
            "market rate for a replacement executive. Verified against payroll registers."
        ),
        category="compensation",
        amount=Decimal("1400000"),
        source_locator="qoe-findings.txt#adjustment-1",
    ),
    dict(
        bridge_layer="management",
        title="One-time ERP implementation costs",
        description=(
            "Non-recurring consulting and migration costs for the ERP cutover completed "
            "November 2025. Invoices reviewed; no further spend budgeted."
        ),
        category="non_recurring",
        amount=Decimal("600000"),
        source_locator="qoe-findings.txt#adjustment-2",
    ),
    dict(
        bridge_layer="sponsor",
        title="Pricing initiative run-rate credit (unverified)",
        description=(
            "Management-proposed credit for a price increase announced December 2025 but not "
            "yet effective. Rests on renewal behavior that has not occurred; treat as an "
            "aggressive sponsor-case item."
        ),
        category="run_rate",
        amount=Decimal("900000"),
        source_locator="qoe-findings.txt#adjustment-3",
    ),
]

_DATA_ROOM_DOCS = [
    ("cim-extract.txt", "CIM extract — Project Meridian"),
    ("customer-contracts.txt", "Top customer contract summary"),
    ("qoe-findings.txt", "Quality of earnings — preliminary findings"),
]


def read_template(name: str) -> bytes:
    """Return a bundled template file's bytes; only whitelisted names resolve."""
    if name not in TEMPLATE_FILES:
        raise KeyError(name)
    return (EXAMPLE_DIR / name).read_bytes()


def _resolve_organization(
    session: Session, organization_id: str | None, actor_id: str
) -> Organization:
    if organization_id:
        organization = session.get(Organization, organization_id)
        if organization is None:
            raise ValueError(f"Organization '{organization_id}' not found")
        return organization
    # Auth-disabled demo mode: reuse (or create) a clearly named shared demo tenant.
    existing = session.scalar(select(Organization).where(Organization.slug == _DEMO_ORG_SLUG))
    if existing is not None:
        return existing
    return deal_workflow_service.create_organization(
        session,
        OrganizationCreate(name=_DEMO_ORG_NAME, slug=_DEMO_ORG_SLUG),
        ActorContext(actor_id=actor_id, display_name="Example deal loader"),
    )


def _resolve_fund(session: Session, organization: Organization, actor: ActorContext) -> Fund:
    existing = session.scalar(
        select(Fund).where(
            Fund.organization_id == organization.id, Fund.name == _DEMO_FUND_NAME
        )
    )
    if existing is not None:
        return existing
    return deal_workflow_service.create_fund(
        session, organization.id, FundCreate(name=_DEMO_FUND_NAME, strategy="buyout"), actor
    )


def _next_deal_code(session: Session, organization_id: str) -> str:
    count = session.scalar(
        select(func.count())
        .select_from(Deal)
        .where(Deal.organization_id == organization_id, Deal.code.like(f"{_DEAL_CODE_PREFIX}-%"))
    ) or 0
    return f"{_DEAL_CODE_PREFIX}-{count + 1}"


def load_example_deal(
    session: Session,
    *,
    organization_id: str | None = None,
    actor_id: str = "demo.user",
    actor_name: str = "Demo user",
) -> dict:
    """Create a fresh example deal end to end and return its identifiers."""
    organization = _resolve_organization(session, organization_id, actor_id)
    actor = ActorContext(
        actor_id=actor_id, display_name=actor_name, organization_id=organization.id
    )
    fund = _resolve_fund(session, organization, actor)

    code = _next_deal_code(session, organization.id)
    workspace = workspace_service.create_workspace(
        session,
        WorkspaceCreate(
            name=f"Project Meridian ({code}) — Example Deal",
            deal_type="buyout",
            investment_question=(
                "Should the fund acquire Meridian Compliance Software at a price consistent "
                "with verified normalized EBITDA, given customer concentration and the "
                "unproven pricing initiative?"
            ),
        ),
        organization_id=organization.id,
    )
    deal = deal_workflow_service.create_deal(
        session,
        fund.id,
        DealCreate(
            code=code,
            name="Project Meridian (Example)",
            target_company=_TARGET_NAME,
            deal_type="buyout",
            workspace_id=workspace.id,
            owner_actor_id=actor.actor_id,
            summary=(
                "Bundled fictional example deal. Every artifact below was loaded through the "
                "same import, provenance, and approval pipeline used for real deals."
            ),
        ),
        actor,
    )

    underwriting_data_service.create_private_target(
        session,
        workspace.id,
        PrivateTargetCreate(
            name=_TARGET_NAME,
            sector="Compliance software",
            fiscal_year_end="12-31",
            description=(
                "Fictional mid-market compliance software vendor used as the packaged example "
                "deal. All figures are synthetic and labeled as such in every document."
            ),
        ),
    )

    csv_bytes = read_template("management_financials.csv")
    rows = underwriting_data_service.parse_financial_csv(csv_bytes, "management_financials.csv")
    import_result = underwriting_data_service.import_financial_rows(
        session,
        workspace.id,
        FinancialImportCreate(
            source_name="FY2024–FY2025 management accounts (example)",
            filename="management_financials.csv",
            content_type="text/csv",
            rows=rows,
            created_by=_ANALYST,
        ),
        raw_input_hash=hashlib.sha256(csv_bytes).hexdigest(),
        byte_size=len(csv_bytes),
        actor_id=_ANALYST,
    )
    snapshot = import_result["snapshot"]

    # Proposed by a distinct fictional analyst so the visitor (a different actor) can
    # exercise the four-eyes approval flow themselves.
    for adjustment in _QOE_ADJUSTMENTS:
        underwriting_data_service.create_qoe_adjustment(
            session,
            workspace.id,
            QoEAdjustmentCreate(
                period_end=date(2025, 12, 31),
                source_snapshot_id=snapshot.id,
                created_by=_ANALYST,
                **adjustment,
            ),
        )

    for filename, title in _DATA_ROOM_DOCS:
        deal_intelligence_service.ingest_text_document(
            session,
            deal.id,
            DocumentTextCreate(
                filename=filename,
                title=title,
                text=read_template(filename).decode("utf-8"),
                document_metadata={"origin": "bundled_example", "synthetic": True},
            ),
            actor,
        )

    return {
        "organization_id": organization.id,
        "fund_id": fund.id,
        "deal_id": deal.id,
        "workspace_id": workspace.id,
        "deal_code": deal.code,
        "import_status": snapshot.status,
        "open_exceptions": import_result["open_exception_count"],
    }
