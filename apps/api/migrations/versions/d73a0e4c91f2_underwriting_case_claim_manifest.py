"""freeze approved private claims into underwriting case versions

Revision ID: d73a0e4c91f2
Revises: 9c4f32a7b8e1
Create Date: 2026-07-14 01:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d73a0e4c91f2"
down_revision: str | Sequence[str] | None = "9c4f32a7b8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_MANIFEST_HASH = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("underwriting_case_versions")
    }
    if "approved_claim_ids" not in existing:
        op.add_column(
            "underwriting_case_versions",
            sa.Column(
                "approved_claim_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    if "approved_claim_manifest" not in existing:
        op.add_column(
            "underwriting_case_versions",
            sa.Column(
                "approved_claim_manifest",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    if "claim_manifest_hash" not in existing:
        op.add_column(
            "underwriting_case_versions",
            sa.Column(
                "claim_manifest_hash",
                sa.String(length=64),
                nullable=False,
                server_default=_EMPTY_MANIFEST_HASH,
            ),
        )


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("underwriting_case_versions")
    }
    for column_name in (
        "claim_manifest_hash",
        "approved_claim_manifest",
        "approved_claim_ids",
    ):
        if column_name in existing:
            op.drop_column("underwriting_case_versions", column_name)
