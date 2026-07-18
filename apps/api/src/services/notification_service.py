"""Project the append-only workflow audit outbox into in-app notifications.

This mirrors ``webhook_service.queue_for_audit_event``: the workflow audit stream is the single
source of truth, and downstream fan-outs (webhooks, notifications) are idempotent, at-least-once
consumers of it. ``sync_from_audit`` reads audit rows not yet mapped for an organization and
creates one ``Notification`` per event, deduplicated by ``source_audit_event_id`` so re-running the
sync never duplicates a row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import WorkflowAuditEvent
from src.models.notification import Notification
from src.services import review_inbox_service
from src.services.common import NotFound

# Human-facing title/body for each audit action. Unmapped actions fall back to a humanized
# rendering so a newly added event type still surfaces something legible rather than nothing.
_EVENT_TEMPLATES: dict[str, tuple[str, str]] = {
    "organization.created": ("Organization created", "A new organization workspace was created."),
    "fund.created": ("Fund created", "A new fund was added."),
    "deal.created": ("Deal created", "A new deal entered the pipeline."),
    "deal.updated": ("Deal updated", "Deal details were changed."),
    "deal.stage_transitioned": ("Deal advanced", "A deal moved to a new pipeline stage."),
    "stage_gate.created": ("Stage gate added", "A new stage gate was created."),
    "stage_gate.resolved": ("Stage gate resolved", "A stage gate was resolved."),
    "deal_team.added": ("Team member added", "Someone joined the deal team."),
    "deal_team.updated": ("Team member updated", "A deal team membership changed."),
    "workstream.created": ("Workstream created", "A new diligence workstream was added."),
    "workstream.updated": ("Workstream updated", "A diligence workstream changed."),
    "milestone.created": ("Milestone created", "A new milestone was scheduled."),
    "milestone.updated": ("Milestone updated", "A milestone changed."),
    "task.created": ("Task created", "A new task was assigned."),
    "task.updated": ("Task updated", "A task changed."),
    "diligence_request.created": ("Diligence request created", "A new diligence request was drafted."),
    "diligence_request.sent": ("Diligence request sent", "A diligence request was issued."),
    "diligence_request.accepted": ("Diligence request accepted", "A diligence response was accepted."),
    "diligence_request.rejected": ("Diligence request rejected", "A diligence response was rejected."),
    "diligence_response.added": ("Diligence response added", "A response was submitted to a request."),
    "diligence_attachment.added": ("Attachment added", "A document was attached to a request."),
    "ledger_entry.created": ("Ledger entry created", "A thesis, risk, or issue was logged."),
    "ledger_entry.revised": ("Ledger entry revised", "A ledger entry was revised."),
    "ic_packet.created": ("IC packet created", "A new investment-committee packet was created."),
    "ic_packet.readiness_checked": ("IC packet readiness checked", "An IC packet readiness check ran."),
    "ic_packet.submitted": ("IC packet submitted", "An IC packet was submitted for review."),
    "ic_packet.export_manifest_created": ("IC packet exported", "An IC packet export was generated."),
    "ic_comment.created": ("IC comment added", "A comment was left on an IC packet."),
    "ic_comment.resolved": ("IC comment resolved", "An IC comment was resolved."),
    "comment.created": ("Comment added", "A comment was posted on a governed artifact."),
    "comment.mentioned": ("You were mentioned", "Someone mentioned you in a comment."),
    "comment.resolved": ("Comment resolved", "A comment thread was resolved."),
    "ic_decision.recorded": ("IC decision recorded", "The investment committee recorded a decision."),
    "condition.resolved": ("Closing condition resolved", "A condition-to-close was resolved."),
    "webhook.endpoint.created": ("Webhook created", "A webhook endpoint was registered."),
    "webhook.endpoint.updated": ("Webhook updated", "A webhook endpoint was changed."),
    "watchlist.filing_detected": ("New filing detected", "A watched company filed a new report."),
}


def render(event: WorkflowAuditEvent) -> tuple[str, str]:
    """Map an audit event to a (title, body) pair, with a humanized fallback."""
    template = _EVENT_TEMPLATES.get(event.action)
    if template is not None:
        title, body = template
    else:
        title = event.action.replace(".", " ").replace("_", " ").strip().capitalize()
        body = f"{event.entity_type} {event.entity_id}"
    if event.actor_display_name:
        body = f"{body} (by {event.actor_display_name})"
    return title, body


def sync_from_audit(session: Session, organization_id: str) -> list[Notification]:
    """Create notifications for this org's audit events that are not yet mapped.

    Idempotent and at-least-once: dedup is enforced both by selecting only unmapped events and by
    the ``uq_notifications_audit_event`` unique constraint, so concurrent syncs cannot duplicate.
    """
    already_mapped = (
        select(Notification.source_audit_event_id)
        .where(
            Notification.organization_id == organization_id,
            Notification.source_audit_event_id.is_not(None),
        )
        .scalar_subquery()
    )
    events = session.scalars(
        select(WorkflowAuditEvent)
        .where(
            WorkflowAuditEvent.organization_id == organization_id,
            WorkflowAuditEvent.id.not_in(already_mapped),
        )
        .order_by(WorkflowAuditEvent.created_at, WorkflowAuditEvent.id)
    ).all()

    created: list[Notification] = []
    for event in events:
        title, body = render(event)
        # A ``comment.mentioned`` event carries the mentioned member's id in ``detail`` so the
        # projection becomes a directed, per-recipient notification (G41); every other event maps
        # to an organization-wide notification (recipient left ``None``).
        recipient_user_id = (
            (event.detail or {}).get("mentioned_user_id")
            if event.action == "comment.mentioned"
            else None
        )
        notification = Notification(
            organization_id=event.organization_id,
            actor_id=event.actor_id,
            recipient_user_id=recipient_user_id,
            event_type=event.action,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            title=title,
            body=body,
            source_audit_event_id=event.id,
        )
        try:
            # A savepoint contains a losing concurrent mapping to THIS row only. A full
            # session.rollback() here discarded every earlier flushed-but-uncommitted row in
            # the batch while still reporting them in `created` — phantom counts.
            with session.begin_nested():
                session.add(notification)
                session.flush()
        except IntegrityError:
            # A concurrent consumer already mapped this event; the unique constraint held.
            continue
        created.append(notification)
    session.commit()
    return created


def _recipient_filter(user_id: str | None):
    """Directed notifications (``recipient_user_id`` set) are visible ONLY to their recipient.

    Broadcast rows (recipient ``None``) are visible to the whole organization. Without this
    filter any member could read — and dismiss — another member's "you were mentioned" rows.
    """
    if user_id is None:
        return Notification.recipient_user_id.is_(None)
    return or_(
        Notification.recipient_user_id.is_(None),
        Notification.recipient_user_id == user_id,
    )


def list_notifications(
    session: Session,
    organization_id: str,
    *,
    unread_only: bool = False,
    user_id: str | None = None,
) -> list[Notification]:
    statement = select(Notification).where(
        Notification.organization_id == organization_id,
        _recipient_filter(user_id),
    )
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    return list(session.scalars(statement.order_by(Notification.created_at.desc(), Notification.id)))


def unread_count(session: Session, organization_id: str, *, user_id: str | None = None) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.organization_id == organization_id,
                Notification.read_at.is_(None),
                _recipient_filter(user_id),
            )
        )
        or 0
    )


# --- G77: per-user digests (computed on read; no schema, no delivery state) -------------------

DIGEST_WINDOWS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


def _as_utc(value: datetime) -> datetime:
    """Normalize DB datetimes for arithmetic: SQLite returns naive (UTC) rows, Postgres aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_digest(
    session: Session,
    organization_id: str,
    *,
    user_id: str | None,
    window: str = "daily",
    now: datetime | None = None,
) -> dict:
    """Roll the window's notifications + review-inbox aging into one per-user summary.

    Computed entirely on read: nothing is delivered, persisted, or marked read — the digest is a
    summary OVER the live notification list, never a re-delivery, so building it twice is
    idempotent and ``read_at`` is untouched. Deterministic given an injected ``now``; the window
    is the half-open interval ``(now - window, now]`` (a row created exactly one window ago has
    aged out).

    Privacy: rows are filtered through the same ``_recipient_filter`` as the live list, so a
    user's digest may include their OWN directed rows (mentions) but never another member's.
    """
    if window not in DIGEST_WINDOWS:
        raise ValueError(f"window must be one of {sorted(DIGEST_WINDOWS)} (got '{window}')")
    until = _as_utc(now) if now is not None else now_utc()
    since = until - DIGEST_WINDOWS[window]

    # Window bounds are applied in Python over normalized datetimes rather than in SQL so the
    # comparison semantics are identical on SQLite (naive storage) and Postgres (aware).
    rows = [
        row
        for row in session.scalars(
            select(Notification)
            .where(
                Notification.organization_id == organization_id,
                _recipient_filter(user_id),
            )
            .order_by(Notification.created_at.desc(), Notification.id)
        )
        if since < _as_utc(row.created_at) <= until
    ]

    groups: dict[str, dict] = {}
    for row in rows:  # newest-first, so the first row seen per type carries the latest title
        group = groups.setdefault(
            row.event_type, {"event_type": row.event_type, "count": 0, "latest_title": row.title}
        )
        group["count"] += 1
    by_event_type = sorted(groups.values(), key=lambda item: (-item["count"], item["event_type"]))

    directed_count = (
        sum(1 for row in rows if row.recipient_user_id == user_id) if user_id else 0
    )

    planes = review_inbox_service.PLANES
    if user_id:
        aging = review_inbox_service.aging_report(session, organization_id, user_id, now=until)
        inbox = {
            "total": aging["total"],
            "counts_by_plane": {plane: aging["planes"][plane]["count"] for plane in planes},
            "oldest_age_hours": max(
                (
                    aging["planes"][plane]["oldest_age_hours"]
                    for plane in planes
                    if aging["planes"][plane]["oldest_age_hours"] is not None
                ),
                default=None,
            ),
            "sla": {
                "total_breaches": aging["total_breaches"],
                "breaches_by_plane": {
                    plane: len(aging["planes"][plane]["breaches"]) for plane in planes
                },
            },
        }
    else:
        # No signed-in user => no actionable review queue to summarize (my_reviews requires an
        # actor); an explicit all-zero block keeps the digest shape stable.
        inbox = {
            "total": 0,
            "counts_by_plane": {plane: 0 for plane in planes},
            "oldest_age_hours": None,
            "sla": {
                "total_breaches": 0,
                "breaches_by_plane": {plane: 0 for plane in planes},
            },
        }

    return {
        "organization_id": organization_id,
        "user_id": user_id,
        "window": window,
        "since": since,
        "until": until,
        "total": len(rows),
        "by_event_type": by_event_type,
        "directed_count": directed_count,
        "inbox": inbox,
    }


def mark_read(
    session: Session,
    notification_id: str,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> Notification:
    """Flip ``read_at`` for a notification, scoped to its organization when known.

    ``organization_id`` is enforced only when supplied (an authenticated request), keeping the
    tenant boundary non-enumerable while preserving zero-config local development. A directed
    notification can be marked read only by its recipient — anyone else gets the same 404 as a
    nonexistent id, so directed rows are not enumerable either.
    """
    notification = session.get(Notification, notification_id)
    if (
        notification is None
        or (organization_id is not None and notification.organization_id != organization_id)
        or (
            notification.recipient_user_id is not None
            and notification.recipient_user_id != user_id
        )
    ):
        raise NotFound(f"Notification '{notification_id}' not found")
    if notification.read_at is None:
        notification.read_at = now_utc()
        session.commit()
        session.refresh(notification)
    return notification
