"""Pytest fixtures. Sets a throwaway SQLite DB before importing the app.

Unit tests run offline. Integration tests hit live SEC EDGAR and are skipped when it's unreachable.
"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp().replace("\\", "/")
os.environ["LLM_MODE"] = "mock"
os.environ["AUTO_SEED"] = "false"
os.environ["SCHEMA_MANAGEMENT"] = "create_all"
os.environ["AUTH_REQUIRED"] = "false"
os.environ["AUTH_ALLOW_REGISTRATION"] = "true"
# Default to a throwaway SQLite file so the suite runs offline with zero setup.
# CI's Postgres matrix (G36) points DEALLENS_TEST_DATABASE_URL at a Postgres service
# container so the identical suite also runs on real Postgres; honor it when present.
# A dedicated var (not a bare DATABASE_URL) keeps a stray local DATABASE_URL from ever
# redirecting the test run at a real database.
os.environ["DATABASE_URL"] = os.environ.get(
    "DEALLENS_TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.sqlite3"
)
os.environ.setdefault(
    "SEC_USER_AGENT", "DealLens Diligence Lab (portfolio test) contact@example.com"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# A stable large-cap with a reliable 10-K + XBRL, used for integration tests.
TEST_TICKER = "MSFT"
TEST_PEERS = ["ORCL", "CRM"]


@pytest.fixture(scope="session")
def client():
    from src.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def sec_online() -> bool:
    from src.services import edgar_client

    try:
        edgar_client.resolve_ticker("AAPL")
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_workspace_id(client, sec_online) -> str:
    """Create one real workspace (ingest + full analysis) reused across integration tests."""
    if not sec_online:
        pytest.skip("SEC EDGAR unreachable; skipping live integration test")
    resp = client.post(
        "/api/workspaces",
        json={"ticker": TEST_TICKER, "deal_type": "public_equity"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]
