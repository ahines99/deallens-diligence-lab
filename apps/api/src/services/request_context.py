"""Request-scoped context shared with layers that have no Request object (G80).

The identity middleware stamps the resolved principal's organization id here so deep layers —
notably the LLM provider's cost-telemetry seam — can attribute work to a tenant without
threading the principal through every call signature. Background/worker paths that never pass
through the middleware simply see ``None``; consumers must treat that as "untagged", never
guess an organization.
"""
from __future__ import annotations

from contextvars import ContextVar

current_organization_id: ContextVar[str | None] = ContextVar(
    "current_organization_id", default=None
)
