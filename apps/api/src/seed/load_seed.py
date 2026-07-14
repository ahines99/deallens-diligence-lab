"""Seed demo workspaces from REAL SEC data (multi-sector), so the app opens fully populated.

Run directly:  python -m src.seed.load_seed
Requires network access to SEC EDGAR. Each demo ingests a company + peers and runs the full analysis.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.session import SessionLocal, prepare_schema
from src.config import settings
from src.models import Organization, Workspace
from src.schemas.workspace import WorkspaceCreate
from src.services import analysis_service, financial_benchmark_service, workspace_service

logger = logging.getLogger("deallens.seed")

# (target ticker, deal_type, peer tickers) — a multi-sector demo set.
DEMOS = [
    ("MSFT", "public_equity", ["GOOGL", "ORCL", "CRM"]),
    ("CRWD", "software_platform", ["PANW", "ZS", "S"]),
]


class SeedConfigurationError(RuntimeError):
    """Raised when secure tenant ownership for demo data cannot be inferred."""


def _resolve_seed_organization_id(session: Session) -> str | None:
    requested_slug = settings.seed_organization_slug.strip()
    if requested_slug:
        organization = session.scalar(
            select(Organization).where(Organization.slug == requested_slug)
        )
        if organization is None:
            raise SeedConfigurationError(
                f"SEED_ORGANIZATION_SLUG '{requested_slug}' does not match an organization"
            )
        return organization.id

    organizations = list(session.scalars(select(Organization).order_by(Organization.created_at)))
    if len(organizations) == 1:
        return organizations[0].id
    if len(organizations) > 1:
        raise SeedConfigurationError(
            "Multiple organizations exist; set SEED_ORGANIZATION_SLUG before seeding"
        )
    if settings.auth_required:
        raise SeedConfigurationError(
            "Register the first owner before seeding authenticated demo data"
        )
    return None


def seed_demo(
    session: Session,
    ticker: str,
    deal_type: str,
    peers: list[str],
    *,
    organization_id: str | None,
) -> Workspace:
    ws = workspace_service.create_workspace(
        session,
        WorkspaceCreate(ticker=ticker, deal_type=deal_type),
        organization_id=organization_id,
    )
    if peers:
        financial_benchmark_service.add_comps_by_ticker(session, ws.id, peers)
        session.commit()
        analysis_service.run_full_analysis(session, ws.id)
    session.refresh(ws)
    return ws


def seed_all_if_empty(session: Session) -> list[str]:
    if session.scalar(select(Workspace)) is not None:
        return []
    organization_id = _resolve_seed_organization_id(session)
    created: list[str] = []
    for ticker, deal_type, peers in DEMOS:
        try:
            ws = seed_demo(
                session,
                ticker,
                deal_type,
                peers,
                organization_id=organization_id,
            )
            created.append(ws.id)
            logger.info("Seeded demo workspace for %s: %s", ticker, ws.id)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Failed to seed %s: %s", ticker, exc)
    return created


def main() -> None:
    prepare_schema()
    with SessionLocal() as session:
        existing = session.scalar(select(Workspace))
        if existing is not None:
            print(f"Workspaces already present (e.g. {existing.id}); nothing to seed.")
            return
        ids = seed_all_if_empty(session)
        if ids:
            print(f"Seeded {len(ids)} demo workspace(s) from live SEC data: {', '.join(ids)}")
        else:
            print("No workspaces seeded (network unavailable?).")


if __name__ == "__main__":
    main()
