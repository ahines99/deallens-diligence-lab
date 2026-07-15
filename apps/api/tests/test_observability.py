"""Coverage for G35 observability: Prometheus /metrics, request-ID, structured JSON logs."""
from __future__ import annotations

import json
import logging
import re

from src.config import settings
from src.observability import (
    JsonLogFormatter,
    path_template,
    reset_request_id,
    set_request_id,
)


def _counter_value(body: str, *, method: str, path: str, status: str) -> int:
    """Sum http_requests_total lines matching the given label triple."""
    total = 0
    for line in body.splitlines():
        if not line.startswith("http_requests_total{"):
            continue
        if (
            f'method="{method}"' in line
            and f'path="{path}"' in line
            and f'status="{status}"' in line
        ):
            total += int(line.rsplit(" ", 1)[1])
    return total


def test_metrics_endpoint_exposes_prometheus_text(client):
    # Generate at least one request so a counter series exists.
    client.get("/api/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]
    body = response.text
    assert "# HELP http_requests_total" in body
    assert "# TYPE http_requests_total counter" in body
    assert "http_requests_total{" in body
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert "http_request_duration_seconds_bucket{" in body
    assert 'le="+Inf"' in body


def test_metrics_is_public_even_when_auth_required(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    # A guarded API route is 401 without a principal, but /metrics stays open to scrapers.
    assert client.get("/api/workspaces").status_code == 401
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_request_id_is_echoed_when_supplied(client):
    response = client.get("/api/health", headers={"X-Request-ID": "abc"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "abc"


def test_request_id_is_generated_when_absent(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    generated = response.headers.get("X-Request-ID")
    assert generated
    # A generated id is a uuid4 hex, distinct from any caller-supplied value.
    assert re.fullmatch(r"[0-9a-f]{32}", generated)

    second = client.get("/api/health")
    assert second.headers["X-Request-ID"] != generated


def test_request_counter_increments_across_requests(client):
    before = _counter_value(
        client.get("/metrics").text, method="GET", path="/api/health", status="200"
    )
    for _ in range(3):
        assert client.get("/api/health").status_code == 200
    after = _counter_value(
        client.get("/metrics").text, method="GET", path="/api/health", status="200"
    )
    assert after - before == 3


def test_path_template_collapses_identifiers():
    assert path_template("/api/workspaces/9f8e7d6c5b4a39281706150403020100/filings") == (
        "/api/workspaces/{id}/filings"
    )
    # The /api/v1 version alias folds onto /api so both surfaces share one series.
    assert path_template("/api/v1/health") == "/api/health"
    assert path_template("/api/health") == "/api/health"


def test_json_log_formatter_emits_parseable_json_with_request_id():
    formatter = JsonLogFormatter()
    token = set_request_id("req-123")
    try:
        record = logging.LogRecord(
            name="deallens.jobs",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="processed %d job(s)",
            args=(2,),
            exc_info=None,
        )
        record.workspace_id = "ws-1"  # structured extra
        rendered = formatter.format(record)
    finally:
        reset_request_id(token)

    payload = json.loads(rendered)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "deallens.jobs"
    assert payload["message"] == "processed 2 job(s)"
    assert payload["request_id"] == "req-123"
    assert payload["workspace_id"] == "ws-1"
    assert payload["timestamp"].endswith("Z")


def test_json_log_formatter_omits_request_id_when_unbound():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="deallens",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="no correlation id here",
        args=(),
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert "request_id" not in payload
    assert payload["level"] == "WARNING"
