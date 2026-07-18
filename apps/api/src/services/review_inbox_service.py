"""G42 — "My reviews" inbox: one queue spanning the four review planes.

Aggregates the pending-and-reviewable-by-this-actor work of one organization across four planes:

* ``qoe``       — proposed QoE adjustments awaiting a decision (``decide_qoe_adjustment``)
* ``claim``     — extracted structured claims awaiting review (``review_claim``)
* ``diligence`` — diligence requests whose latest response awaits acceptance
                  (``review_diligence_request``)
* ``ic_comment``— open blocking IC comments awaiting resolution (``resolve_ic_comment``)

Every plane honours the four-eyes rule its own decision path enforces: an actor never sees an item
they themselves proposed, authored, or responded to, because that plane would reject their review.
The queue is therefore *actionable* — every item is one the actor is actually permitted to decide.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_intelligence import StructuredClaim
from src.models.deal_workflow import (
    Deal,
    DiligenceRequest,
    DiligenceResponse,
    ICComment,
    ICPacket,
)
from src.models.underwriting_data import QoEAdjustment
from src.models.workspace import Workspace

PLANES = ("qoe", "claim", "diligence", "ic_comment")

# G78 — default per-plane SLA thresholds (hours an item may wait before it counts as a breach).
# Deliberately NOT settings: thresholds are a per-request query-param concern with these
# documented defaults, so different reviewers/reports can probe different tolerances without a
# deployment change. Rationale: blocking IC comments gate a live packet (tightest); QoE
# adjustments and extracted claims are internal four-eyes reviews (a standard three business
# days); diligence responses involve an external counterparty round-trip (loosest).
DEFAULT_SLA_HOURS: dict[str, float] = {
    "qoe": 72.0,
    "claim": 72.0,
    "diligence": 120.0,
    "ic_comment": 48.0,
}


class ReviewInboxError(ValueError):
    """A user-correctable review-inbox request error (mapped to HTTP 4xx)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _as_utc(value: datetime) -> datetime:
    """Normalize DB datetimes (SQLite rows are naive UTC, Postgres aware) so sorting and age
    arithmetic never mix naive and aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _item(
    plane: str,
    entity_id: str,
    title: str,
    deal_or_workspace: str,
    created_at: datetime,
    url_hint: str,
) -> dict:
    return {
        "plane": plane,
        "id": entity_id,
        "title": title,
        "deal_or_workspace": deal_or_workspace,
        "created_at": _as_utc(created_at),
        "url_hint": url_hint,
    }


def _qoe_items(session: Session, organization_id: str, actor_id: str) -> list[dict]:
    # QoE adjustments live on a workspace; the tenant boundary is the workspace's organization
    # (set when a deal binds the workspace). Four-eyes: exclude adjustments the actor proposed.
    rows = list(
        session.scalars(
            select(QoEAdjustment)
            .join(Workspace, Workspace.id == QoEAdjustment.workspace_id)
            .where(
                Workspace.organization_id == organization_id,
                QoEAdjustment.status == "proposed",
                QoEAdjustment.created_by != actor_id,
            )
            .order_by(QoEAdjustment.created_at, QoEAdjustment.id)
        )
    )
    return [
        _item(
            "qoe",
            adjustment.id,
            adjustment.title,
            adjustment.workspace_id,
            adjustment.created_at,
            f"/api/workspaces/{adjustment.workspace_id}/underwriting/qoe-adjustments",
        )
        for adjustment in rows
    ]


def _claim_items(session: Session, organization_id: str, actor_id: str) -> list[dict]:
    # Only the latest revision of each logical claim is reviewable; an unreviewed latest revision
    # authored by someone other than this actor awaits their (four-eyes) approve/reject.
    claims = list(
        session.scalars(
            select(StructuredClaim)
            .join(Deal, Deal.id == StructuredClaim.deal_id)
            .where(Deal.organization_id == organization_id)
            .order_by(StructuredClaim.logical_claim_id, StructuredClaim.revision.desc())
        )
    )
    latest: dict[str, StructuredClaim] = {}
    for claim in claims:
        latest.setdefault(claim.logical_claim_id, claim)
    items: list[dict] = []
    for claim in latest.values():
        if claim.review_status != "unreviewed":
            continue
        if claim.created_by_actor_id == actor_id:
            continue
        items.append(
            _item(
                "claim",
                claim.id,
                f"{claim.field_name}: {claim.value_text}"[:240],
                claim.deal_id,
                claim.created_at,
                f"/api/deals/{claim.deal_id}/intelligence/claims",
            )
        )
    items.sort(key=lambda entry: (entry["created_at"], entry["id"]))
    return items


def _diligence_items(session: Session, organization_id: str, actor_id: str) -> list[dict]:
    requests = list(
        session.scalars(
            select(DiligenceRequest)
            .join(Deal, Deal.id == DiligenceRequest.deal_id)
            .where(
                Deal.organization_id == organization_id,
                DiligenceRequest.status.in_(("responded", "under_review")),
            )
            .order_by(DiligenceRequest.created_at, DiligenceRequest.id)
        )
    )
    items: list[dict] = []
    for request in requests:
        latest = session.scalar(
            select(DiligenceResponse)
            .where(DiligenceResponse.request_id == request.id)
            .order_by(DiligenceResponse.sequence.desc())
            .limit(1)
        )
        if latest is None:
            continue
        # Four-eyes: the response author cannot accept their own response.
        if latest.responded_by_actor_id == actor_id:
            continue
        items.append(
            _item(
                "diligence",
                request.id,
                request.title,
                request.deal_id,
                request.last_response_at or latest.submitted_at,
                f"/api/diligence-requests/{request.id}/review",
            )
        )
    return items


def _ic_comment_items(session: Session, organization_id: str, actor_id: str) -> list[dict]:
    # A blocking, still-open IC comment must be resolved by a *second* actor; surface those the
    # signed-in actor did not author.
    rows = list(
        session.scalars(
            select(ICComment)
            .join(ICPacket, ICPacket.id == ICComment.packet_id)
            .join(Deal, Deal.id == ICPacket.deal_id)
            .where(
                Deal.organization_id == organization_id,
                ICComment.status == "open",
                ICComment.blocking.is_(True),
                ICComment.author_actor_id.is_not(None),
                ICComment.author_actor_id != actor_id,
            )
            .order_by(ICComment.created_at, ICComment.id)
        )
    )
    items: list[dict] = []
    for comment in rows:
        packet = session.get(ICPacket, comment.packet_id)
        title = (comment.body or comment.section_path or "IC comment").strip()[:240]
        items.append(
            _item(
                "ic_comment",
                comment.id,
                title,
                packet.deal_id if packet else comment.packet_id,
                comment.created_at,
                f"/api/ic-comments/{comment.id}/resolve",
            )
        )
    return items


def _plane_items(session: Session, organization_id: str, actor_id: str) -> dict[str, list[dict]]:
    """The four planes' pending items for one actor — shared by ``my_reviews`` and
    ``aging_report`` so the aging view can never diverge from the inbox (same tenant scoping,
    same four-eyes exclusions, same item builders)."""
    return {
        "qoe": _qoe_items(session, organization_id, actor_id),
        "claim": _claim_items(session, organization_id, actor_id),
        "diligence": _diligence_items(session, organization_id, actor_id),
        "ic_comment": _ic_comment_items(session, organization_id, actor_id),
    }


def my_reviews(session: Session, organization_id: str, actor_id: str | None) -> dict:
    """Return every pending item across the four review planes awaiting ``actor_id``.

    The result carries a flat, newest-first ``items`` list plus a ``counts_by_plane`` breakdown.
    Everything is organization-scoped; four-eyes exclusions are applied per plane.
    """
    if not actor_id:
        raise ReviewInboxError(
            "actor_id is required to resolve a review inbox", status_code=422
        )
    by_plane = _plane_items(session, organization_id, actor_id)
    items = [entry for plane in PLANES for entry in by_plane[plane]]
    items.sort(key=lambda entry: (entry["created_at"], entry["id"]), reverse=True)
    return {
        "organization_id": organization_id,
        "actor_id": actor_id,
        "items": items,
        "counts_by_plane": {plane: len(by_plane[plane]) for plane in PLANES},
        "total": len(items),
    }


def aging_report(
    session: Session,
    organization_id: str,
    actor_id: str | None,
    *,
    now: datetime | None = None,
    sla_hours: dict[str, float] | None = None,
) -> dict:
    """G78 — per-plane aging over the SAME queue ``my_reviews`` serves, with SLA breaches.

    Ages are measured against each item's inbox timestamp (for diligence that is the latest
    response time — the moment the item began waiting on a reviewer). An item breaches when its
    age strictly exceeds its plane's threshold. ``sla_hours`` overrides individual plane
    thresholds per request (documented defaults in :data:`DEFAULT_SLA_HOURS`); unknown planes or
    non-positive thresholds are rejected. Deterministic given an injected ``now``. Because the
    items come from the shared four-eyes builders, an item the actor proposed is never counted
    as THEIR breach.
    """
    if not actor_id:
        raise ReviewInboxError(
            "actor_id is required to resolve a review inbox", status_code=422
        )
    thresholds = dict(DEFAULT_SLA_HOURS)
    for plane, value in (sla_hours or {}).items():
        if plane not in DEFAULT_SLA_HOURS:
            raise ReviewInboxError(
                f"Unknown review plane '{plane}' (expected one of {', '.join(PLANES)})"
            )
        if not isinstance(value, (int, float)) or value <= 0:
            raise ReviewInboxError(f"sla_hours for plane '{plane}' must be a positive number")
        thresholds[plane] = float(value)

    as_of = _as_utc(now) if now is not None else now_utc()
    by_plane = _plane_items(session, organization_id, actor_id)

    planes: dict[str, dict] = {}
    total = 0
    total_breaches = 0
    for plane in PLANES:
        limit = thresholds[plane]
        ages: list[float] = []
        breaches: list[dict] = []
        for item in by_plane[plane]:
            age = round(
                max(0.0, (as_of - _as_utc(item["created_at"])).total_seconds()) / 3600.0, 2
            )
            ages.append(age)
            if age > limit:
                breaches.append(
                    {
                        "id": item["id"],
                        "title": item["title"],
                        "age_hours": age,
                        "sla_hours": limit,
                    }
                )
        breaches.sort(key=lambda entry: (-entry["age_hours"], entry["id"]))
        planes[plane] = {
            "count": len(ages),
            "oldest_age_hours": max(ages) if ages else None,
            "sla_hours": limit,
            "breaches": breaches,
        }
        total += len(ages)
        total_breaches += len(breaches)

    return {
        "organization_id": organization_id,
        "actor_id": actor_id,
        "as_of": as_of,
        "sla_hours": thresholds,
        "planes": planes,
        "total": total,
        "total_breaches": total_breaches,
    }


__all__ = ["DEFAULT_SLA_HOURS", "ReviewInboxError", "aging_report", "my_reviews", "PLANES"]
