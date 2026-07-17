"""Project the append-only workflow audit outbox into in-app notifications.

This mirrors ``webhook_service.queue_for_audit_event``: the workflow audit stream is the single
source of truth, and downstream fan-outs (webhooks, notifications) are idempotent, at-least-once
consumers of it. ``sync_from_audit`` reads audit rows not yet mapped for an organization and
creates one ``Notification`` per event, deduplicated by ``source_audit_event_id`` so re-running the
sync never duplicates a row.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import WorkflowAuditEvent
from src.models.notification import Notification
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
