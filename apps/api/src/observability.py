"""Observability primitives: in-process Prometheus metrics, JSON logs, request-ID context.

No third-party dependency (no ``prometheus_client``): the metrics registry emits the
Prometheus text exposition format (version 0.0.4) by hand. A request-scoped correlation id
lives in a :class:`contextvars.ContextVar` so it survives across ``await`` boundaries and is
injected into every structured log line.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from contextvars import ContextVar, Token

# --- Request-ID propagation ------------------------------------------------

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Reserved LogRecord attributes we never re-emit as "extra" fields.
_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)


def new_request_id() -> str:
    """Generate a fresh correlation id (used when no inbound X-Request-ID is present)."""
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> Token:
    """Bind ``request_id`` to the current context; returns a token for :func:`reset_request_id`."""
    return _request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    """Return the correlation id bound to the current context, if any."""
    return _request_id.get()


# --- Path templating (metrics cardinality control) --------------------------

_UUID_HEX_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
_UUID_DASHED_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _normalize_segment(segment: str) -> str:
    """Collapse identifier-like path segments to ``{id}`` so metric labels stay low-cardinality."""
    if not segment:
        return segment
    if segment.isdigit():
        return "{id}"
    if _UUID_DASHED_RE.match(segment) or _UUID_HEX_RE.match(segment):
        return "{id}"
    # Session/API tokens and other long id-ish opaque values.
    if len(segment) >= 20 and any(ch.isdigit() for ch in segment):
        return "{id}"
    return segment


def path_template(path: str) -> str:
    """Map a concrete request path to a low-cardinality template.

    ``/api/workspaces/9f.../filings`` becomes ``/api/workspaces/{id}/filings`` and the
    ``/api/v1`` version alias is folded onto ``/api`` so both surfaces share one series.
    """
    if path == "/api/v1":
        path = "/api"
    elif path.startswith("/api/v1/"):
        path = "/api" + path[len("/api/v1"):]
    if path == "/":
        return path
    segments = path.split("/")
    return "/".join(_normalize_segment(seg) for seg in segments) or "/"


# --- Metrics registry -------------------------------------------------------

# Histogram buckets (seconds) covering sub-millisecond handlers up to slow SEC ingests.
_DURATION_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    """Thread-safe counters + a latency histogram, rendered as Prometheus text exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key: (method, path_template, status) -> count
        self._requests: dict[tuple[str, str, str], int] = {}
        # key: (method, path_template) -> {"buckets": [...], "sum": float, "count": int}
        self._durations: dict[tuple[str, str], dict] = {}

    def observe(self, method: str, template: str, status: int, duration_seconds: float) -> None:
        method = method.upper()
        status_label = str(status)
        with self._lock:
            req_key = (method, template, status_label)
            self._requests[req_key] = self._requests.get(req_key, 0) + 1

            dur_key = (method, template)
            entry = self._durations.get(dur_key)
            if entry is None:
                entry = {"buckets": [0] * len(_DURATION_BUCKETS), "sum": 0.0, "count": 0}
                self._durations[dur_key] = entry
            entry["sum"] += duration_seconds
            entry["count"] += 1
            for index, boundary in enumerate(_DURATION_BUCKETS):
                if duration_seconds <= boundary:
                    entry["buckets"][index] += 1

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._durations.clear()

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP http_requests_total Total HTTP requests processed.")
            lines.append("# TYPE http_requests_total counter")
            for (method, template, status), count in sorted(self._requests.items()):
                labels = (
                    f'method="{_escape_label(method)}",'
                    f'path="{_escape_label(template)}",'
                    f'status="{_escape_label(status)}"'
                )
                lines.append(f"http_requests_total{{{labels}}} {count}")

            lines.append(
                "# HELP http_request_duration_seconds HTTP request latency in seconds."
            )
            lines.append("# TYPE http_request_duration_seconds histogram")
            for (method, template), entry in sorted(self._durations.items()):
                base = f'method="{_escape_label(method)}",path="{_escape_label(template)}"'
                cumulative = 0
                for index, boundary in enumerate(_DURATION_BUCKETS):
                    cumulative = entry["buckets"][index]
                    le = _format_float(boundary)
                    lines.append(
                        f'http_request_duration_seconds_bucket{{{base},le="{le}"}} {cumulative}'
                    )
                lines.append(
                    f'http_request_duration_seconds_bucket{{{base},le="+Inf"}} {entry["count"]}'
                )
                lines.append(
                    f"http_request_duration_seconds_sum{{{base}}} {_format_float(entry['sum'])}"
                )
                lines.append(
                    f"http_request_duration_seconds_count{{{base}}} {entry['count']}"
                )
        return "\n".join(lines) + "\n"


def _format_float(value: float) -> str:
    text = repr(float(value))
    return text


CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

# Process-wide registry shared by the middleware and the /metrics route.
METRICS = MetricsRegistry()


# --- Structured JSON logging ------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON with the active request id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # Surface structured extras passed via ``logger.info(..., extra={...})``.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOGRECORD_ATTRS and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(json_logs: bool) -> None:
    """Attach the JSON formatter to the root logger when structured logs are enabled.

    Human-readable logs remain the default (dev-friendly); production sets ``JSON_LOGS=true``.
    """
    if not json_logs:
        return
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    formatter = JsonLogFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
