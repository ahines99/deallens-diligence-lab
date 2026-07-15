"""G43 — Audit-log explorer: organization-level, filterable read over the audit outbox.

``WorkflowAuditEvent`` is the append-only outbox every workflow/IC mutation writes to. This service
exposes an org-scoped, filterable view (actor / entity / date window) plus a CSV export that
neutralizes spreadsheet formula injection (CWE-1236) on every user-controlled field.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.deal_workflow import WorkflowAuditEvent

MAX_LIMIT = 5_000
DEFAULT_LIMIT = 200

_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """Neutralize spreadsheet formula injection (mirrors ``portfolio_service._csv_safe``).

    Actor names, actions, entity values, and serialized detail are user-influenced free text. A
    value beginning with a formula trigger is prefixed with a leading apostrophe so a spreadsheet
    renders it as literal text instead of executing e.g. ``=WEBSERVICE(...)``.
    """
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


def list_events(
    session: Session,
    organization_id: str,
    *,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[WorkflowAuditEvent]:
    """Return audit events for one organization, newest first, narrowed by the given filters."""
    query = select(WorkflowAuditEvent).where(
        WorkflowAuditEvent.organization_id == organization_id
    )
    if actor:
        query = query.where(WorkflowAuditEvent.actor_id == actor)
    if entity_type:
        query = query.where(WorkflowAuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(WorkflowAuditEvent.entity_id == entity_id)
    if since is not None:
        query = query.where(WorkflowAuditEvent.created_at >= since)
    if until is not None:
        query = query.where(WorkflowAuditEvent.created_at <= until)
    query = query.order_by(
        WorkflowAuditEvent.created_at.desc(), WorkflowAuditEvent.id.desc()
    ).limit(min(max(limit, 1), MAX_LIMIT))
    return list(session.scalars(query))


_CSV_FIELDS = (
    "created_at",
    "actor_id",
    "actor_display_name",
    "action",
    "entity_type",
    "entity_id",
    "deal_id",
    "request_id",
    "detail",
)


def export_csv(
    session: Session,
    organization_id: str,
    *,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = MAX_LIMIT,
) -> str:
    """Render the filtered event set as formula-injection-safe CSV text."""
    events = list_events(
        session,
        organization_id,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
        limit=limit,
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        writer.writerow(
            {
                "created_at": event.created_at.isoformat() if event.created_at else "",
                "actor_id": _csv_safe(event.actor_id or ""),
                "actor_display_name": _csv_safe(event.actor_display_name or ""),
                "action": _csv_safe(event.action),
                "entity_type": _csv_safe(event.entity_type),
                "entity_id": _csv_safe(event.entity_id),
                "deal_id": event.deal_id or "",
                "request_id": _csv_safe(event.request_id or ""),
                "detail": _csv_safe(
                    json.dumps(event.detail or {}, sort_keys=True, separators=(",", ":"))
                ),
            }
        )
    return output.getvalue()


__all__ = ["export_csv", "list_events", "DEFAULT_LIMIT", "MAX_LIMIT"]
