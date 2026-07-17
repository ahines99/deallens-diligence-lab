"""Tenant-scoped portfolio analytics assembled from model-of-record tables."""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.deal_workflow import (
    ConditionToClose,
    Deal,
    DealLedgerEntry,
    DealStageGate,
    DealStageTransition,
    DealTask,
    DealWorkstream,
    DiligenceRequest,
    Fund,
    ICPacket,
    Organization,
)
from src.models.target import Target
from src.models.underwriting_data import (
    CanonicalFinancialFact,
    FinancialImportException,
    FinancialReconciliation,
    QoEAdjustment,
    SourceSnapshot,
)
from src.models.underwriting_model import UnderwritingCaseVersion

_TERMINAL_TASKS = {"complete", "cancelled"}
_TERMINAL_REQUESTS = {"accepted", "closed"}
_CLOSED_RISKS = {"mitigated", "rejected", "resolved", "superseded"}
_STAGE_ORDER = (
    "sourcing",
    "screening",
    "initial_review",
    "diligence",
    "ic_review",
    "signing",
    "closed",
    "declined",
)


class PortfolioError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _created_date(value: datetime) -> date:
    return value.date()


def _age_days(value: datetime | date, as_of: date) -> int:
    item_date = value if isinstance(value, date) and not isinstance(value, datetime) else value.date()
    return max(0, (as_of - item_date).days)


def _distribution(values: Iterable[str], total: int) -> list[dict[str, Any]]:
    counts = Counter(values)
    return [
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "count": count,
            "percent": round((count / total * 100) if total else 0.0, 1),
        }
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _component(
    key: str,
    label: str,
    passed: int,
    total: int,
    weight: float,
    empty_explanation: str,
) -> dict[str, Any]:
    score = (passed / total * 100) if total else 0.0
    explanation = (
        f"{passed} of {total} controls are complete" if total else empty_explanation
    )
    return {
        "key": key,
        "label": label,
        "score": round(score, 1),
        "weight": weight,
        "passed": passed,
        "total": total,
        "explanation": explanation,
    }


def _readiness(
    deal: Deal,
    gates: list[DealStageGate],
    tasks: list[DealTask],
    requests: list[DiligenceRequest],
    risks: list[DealLedgerEntry],
    packet: ICPacket | None,
    source_health: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    current_gates = [item for item in gates if item.stage == deal.stage and item.required]
    risk_controls = [item for item in risks if item.severity in {"high", "critical"}]
    components = [
        _component(
            "stage_gates",
            "Stage gates",
            sum(item.status in {"satisfied", "waived"} for item in current_gates),
            len(current_gates),
            0.25,
            "No required stage gates are configured",
        ),
        _component(
            "tasks",
            "Execution tasks",
            sum(item.status in _TERMINAL_TASKS for item in tasks),
            len(tasks),
            0.20,
            "No execution tasks are configured",
        ),
        _component(
            "requests",
            "Diligence requests",
            sum(item.status in _TERMINAL_REQUESTS for item in requests),
            len(requests),
            0.15,
            "No diligence requests are configured",
        ),
        _component(
            "risks",
            "Material risks",
            sum(item.status in _CLOSED_RISKS for item in risk_controls),
            len(risk_controls),
            0.20,
            "No high or critical risks are recorded",
        ),
        _component(
            "sources",
            "Source integrity",
            source_health["ready"],
            source_health["total_sources"],
            0.10,
            "No source snapshots are registered",
        ),
        _component(
            "ic_packet",
            "IC packet",
            int(bool(packet and packet.ready_for_submission)),
            1,
            0.10,
            "No IC packet is assembled",
        ),
    ]
    # Renormalize over components that actually have data: an unconfigured control is "unknown",
    # not "failing", so it must not drag the headline readiness score toward zero. When every
    # control is configured the denominator is the full weight (1.0) and this is a no-op.
    assessed = [item for item in components if item["total"] > 0]
    if assessed:
        weight_sum = sum(item["weight"] for item in assessed)
        score = sum(item["score"] * item["weight"] for item in assessed) / weight_sum
    else:
        score = 0.0
    return round(score, 1), components


def _source_health(snapshots: list[SourceSnapshot], as_of: date) -> dict[str, Any]:
    latest: dict[tuple[str, str], SourceSnapshot] = {}
    for item in snapshots:
        key = (item.source_type, item.source_name)
        current = latest.get(key)
        if current is None or (item.version, item.created_at) > (
            current.version,
            current.created_at,
        ):
            latest[key] = item
    records = list(latest.values())
    statuses = Counter(item.status for item in records)
    ages = [_age_days(item.created_at, as_of) for item in records]
    if not records:
        status = "not_configured"
    elif statuses["failed"]:
        status = "failed"
    elif statuses["partial"] or any(age > 90 for age in ages):
        status = "partial"
    else:
        status = "ready"
    return {
        "status": status,
        "total_sources": len(records),
        "ready": statuses["ready"],
        "partial": statuses["partial"],
        "failed": statuses["failed"],
        "freshest_at": max((item.created_at for item in records), default=None),
        "oldest_age_days": max(ages) if ages else None,
        "stale": any(age > 90 for age in ages),
    }


def _financial_quality(
    facts: list[CanonicalFinancialFact],
    reconciliations: list[FinancialReconciliation],
    exceptions: list[FinancialImportException],
    adjustments: list[QoEAdjustment],
) -> dict[str, Any]:
    # Coverage counts every mapped_* state ("mapped", "mapped_explicit"), matching the import
    # summary and the QoE bridge's `mapping_state LIKE 'mapped%'` filter — an exact "mapped"
    # comparison silently excluded explicitly-mapped facts and disagreed with the bridge.
    mapped = sum(
        item.mapping_state.startswith("mapped") and bool(item.canonical_account)
        for item in facts
    )
    mapping_coverage = round(mapped / len(facts) * 100, 1) if facts else None
    passed = sum(item.status == "passed" for item in reconciliations)
    reconciliation_score = (
        round(passed / len(reconciliations) * 100, 1) if reconciliations else None
    )
    open_exceptions = sum(item.state == "open" for item in exceptions)

    # Base-fact selection mirrors get_qoe_bridge: mapped currency facts only, newest period,
    # then newest fact as a deterministic tie-break. A bare max() over the unordered query
    # result made reported_ebitda nondeterministic when two snapshots shared a period end.
    ebitda_facts = [
        item
        for item in facts
        if (item.canonical_account or "").strip().casefold() == "ebitda"
        and item.mapping_state.startswith("mapped")
        and item.unit == "currency"
        and item.currency is not None
    ]
    latest_ebitda = max(
        ebitda_facts,
        key=lambda item: (item.period_end, item.created_at, item.id),
        default=None,
    )
    reported_ebitda = _number(latest_ebitda.value) if latest_ebitda else None
    period = latest_ebitda.period_end if latest_ebitda else None
    currency = latest_ebitda.currency if latest_ebitda else None
    # Sponsor-adjusted EBITDA is the bridge's sponsor layer: approved management + sponsor
    # adjustments in the bridge currency. Covenant-layer items are a different measure and
    # previously inflated this number relative to GET .../underwriting/qoe-bridge.
    accepted_adjustments = [
        item
        for item in adjustments
        if item.status == "approved"
        and (period is None or item.period_end == period)
        and (currency is None or item.currency == currency)
        and item.bridge_layer in ("management", "sponsor")
    ]
    qoe_amount = sum((_number(item.amount) or 0.0) for item in accepted_adjustments)
    sponsor_ebitda = reported_ebitda + qoe_amount if reported_ebitda is not None else None
    ebitda_variance = (
        sponsor_ebitda - reported_ebitda
        if sponsor_ebitda is not None and reported_ebitda is not None
        else None
    )
    materiality = (
        abs(qoe_amount) / abs(reported_ebitda)
        if reported_ebitda not in {None, 0.0}
        else None
    )

    diagnostics: list[str] = []
    latest_by_statement: dict[str, date] = {}
    currencies = {item.currency for item in facts if item.currency}
    for item in facts:
        latest_by_statement[item.statement] = max(
            latest_by_statement.get(item.statement, item.period_end), item.period_end
        )
        if item.period_type != "instant" and item.period_start is None:
            diagnostics.append(f"{item.statement} contains a duration fact without period_start")
    latest_dates = set(latest_by_statement.values())
    if len(latest_dates) > 1:
        diagnostics.append("Latest imported statement periods do not share one period end")
    if len(currencies) > 1:
        diagnostics.append("Imported facts contain multiple currencies")
    diagnostics = list(dict.fromkeys(diagnostics))
    period_consistent = not diagnostics if facts else None

    return {
        "mapping_coverage": mapping_coverage,
        "mapped_facts": mapped,
        "total_facts": len(facts),
        "reconciliation_score": reconciliation_score,
        "reconciliations_passed": passed,
        "reconciliations_total": len(reconciliations),
        "open_exceptions": open_exceptions,
        "qoe_adjustment_amount": round(qoe_amount, 2),
        "qoe_materiality": round(materiality, 4) if materiality is not None else None,
        "reported_ebitda": reported_ebitda,
        "sponsor_adjusted_ebitda": sponsor_ebitda,
        "ebitda_variance": ebitda_variance,
        "period_consistent": period_consistent,
        "period_diagnostics": diagnostics,
    }


def _latest_cases(records: list[UnderwritingCaseVersion]) -> list[UnderwritingCaseVersion]:
    latest: dict[tuple[str, str], UnderwritingCaseVersion] = {}
    for item in records:
        key = (item.workspace_id, item.case_key)
        current = latest.get(key)
        if current is None or item.version > current.version:
            latest[key] = item
    return list(latest.values())


def _case_payload(item: UnderwritingCaseVersion) -> dict[str, Any]:
    result = item.result or {}
    returns = result.get("returns") or {}
    summary = result.get("summary") or {}
    return {
        "case_key": item.case_key,
        "case_version_id": item.id,
        "version": item.version,
        "created_at": item.created_at,
        "moic": _number(returns.get("moic")),
        "xirr": _number(returns.get("xirr")),
        "minimum_liquidity": _number(summary.get("minimum_liquidity")),
        "first_covenant_breach": summary.get("first_covenant_breach"),
        "first_debt_service_default": summary.get("first_debt_service_default"),
    }


def get_dashboard(
    session: Session,
    organization_id: str,
    *,
    search: str | None = None,
    stage: str | None = None,
    fund_id: str | None = None,
    as_of: date | None = None,
    ic_window_days: int = 30,
) -> dict[str, Any]:
    if session.get(Organization, organization_id) is None:
        raise PortfolioError("Organization not found", 404)
    as_of = as_of or now_utc().date()
    statement = (
        select(Deal, Fund)
        .join(Fund, Fund.id == Deal.fund_id)
        .where(Deal.organization_id == organization_id)
    )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                Deal.code.ilike(pattern),
                Deal.name.ilike(pattern),
                Deal.target_company.ilike(pattern),
            )
        )
    if stage:
        statement = statement.where(Deal.stage == stage)
    if fund_id:
        statement = statement.where(Deal.fund_id == fund_id)
    pairs = list(session.execute(statement.order_by(Deal.created_at.desc())).all())
    deals = [row[0] for row in pairs]
    funds = {row[1].id: row[1] for row in pairs}
    deal_ids = [item.id for item in deals]
    workspace_ids = [item.workspace_id for item in deals if item.workspace_id]

    def related(model, key: str = "deal_id") -> list[Any]:
        if not deal_ids:
            return []
        return list(session.scalars(select(model).where(getattr(model, key).in_(deal_ids))))

    tasks = related(DealTask)
    workstreams = related(DealWorkstream)
    requests = related(DiligenceRequest)
    ledger = related(DealLedgerEntry)
    conditions = related(ConditionToClose)
    gates = related(DealStageGate)
    transitions = related(DealStageTransition)
    packets = related(ICPacket)
    targets = (
        list(session.scalars(select(Target).where(Target.workspace_id.in_(workspace_ids))))
        if workspace_ids
        else []
    )
    snapshots = (
        list(
            session.scalars(
                select(SourceSnapshot).where(SourceSnapshot.workspace_id.in_(workspace_ids))
            )
        )
        if workspace_ids
        else []
    )
    facts = (
        list(
            session.scalars(
                select(CanonicalFinancialFact).where(
                    CanonicalFinancialFact.workspace_id.in_(workspace_ids)
                )
            )
        )
        if workspace_ids
        else []
    )
    reconciliations = (
        list(
            session.scalars(
                select(FinancialReconciliation).where(
                    FinancialReconciliation.workspace_id.in_(workspace_ids)
                )
            )
        )
        if workspace_ids
        else []
    )
    import_exceptions = (
        list(
            session.scalars(
                select(FinancialImportException).where(
                    FinancialImportException.workspace_id.in_(workspace_ids)
                )
            )
        )
        if workspace_ids
        else []
    )
    adjustments = (
        list(
            session.scalars(
                select(QoEAdjustment).where(QoEAdjustment.workspace_id.in_(workspace_ids))
            )
        )
        if workspace_ids
        else []
    )
    cases = (
        _latest_cases(
            list(
                session.scalars(
                    select(UnderwritingCaseVersion).where(
                        UnderwritingCaseVersion.workspace_id.in_(workspace_ids)
                    )
                )
            )
        )
        if workspace_ids
        else []
    )

    by_deal = lambda records: {  # noqa: E731 - compact grouping helper
        deal_id: [item for item in records if item.deal_id == deal_id] for deal_id in deal_ids
    }
    tasks_by_deal = by_deal(tasks)
    streams_by_deal = by_deal(workstreams)
    requests_by_deal = by_deal(requests)
    ledger_by_deal = by_deal(ledger)
    gates_by_deal = by_deal(gates)
    transitions_by_deal = by_deal(transitions)
    packets_by_deal = by_deal(packets)
    target_by_workspace = {item.workspace_id: item for item in targets}

    def by_workspace(records: list[Any]) -> dict[str, list[Any]]:
        grouped: dict[str, list[Any]] = defaultdict(list)
        for item in records:
            grouped[item.workspace_id].append(item)
        return grouped

    snapshots_by_workspace = by_workspace(snapshots)
    facts_by_workspace = by_workspace(facts)
    reconciliation_by_workspace = by_workspace(reconciliations)
    exceptions_by_workspace = by_workspace(import_exceptions)
    adjustments_by_workspace = by_workspace(adjustments)
    cases_by_workspace = by_workspace(cases)

    deal_rows: list[dict[str, Any]] = []
    readiness_values: list[float] = []
    for deal in deals:
        workspace_id = deal.workspace_id or ""
        source_health = _source_health(snapshots_by_workspace[workspace_id], as_of)
        financial_quality = _financial_quality(
            facts_by_workspace[workspace_id],
            reconciliation_by_workspace[workspace_id],
            exceptions_by_workspace[workspace_id],
            adjustments_by_workspace[workspace_id],
        )
        latest_packet = max(
            packets_by_deal[deal.id], key=lambda item: item.version, default=None
        )
        readiness, components = _readiness(
            deal,
            gates_by_deal[deal.id],
            tasks_by_deal[deal.id],
            requests_by_deal[deal.id],
            ledger_by_deal[deal.id],
            latest_packet,
            source_health,
        )
        readiness_values.append(readiness)
        transition = max(
            transitions_by_deal[deal.id], key=lambda item: item.created_at, default=None
        )
        stage_started = transition.created_at if transition else deal.created_at
        target = target_by_workspace.get(workspace_id)
        fund = funds[deal.fund_id]
        deal_rows.append(
            {
                "id": deal.id,
                "code": deal.code,
                "name": deal.name,
                "target_company": deal.target_company,
                "fund_id": deal.fund_id,
                "fund_name": fund.name,
                "strategy": fund.strategy,
                "workspace_id": deal.workspace_id,
                "sector": (target.sector if target and target.sector else "Unclassified"),
                "stage": deal.stage,
                "status": deal.status,
                "owner_actor_id": deal.owner_actor_id,
                "ic_date": deal.ic_date,
                "stage_age_days": _age_days(stage_started, as_of),
                "readiness_score": readiness,
                "readiness_components": components,
                "source_health": source_health,
                "financial_quality": financial_quality,
            }
        )

    upcoming_ic = [
        {
            "deal_id": item.id,
            "code": item.code,
            "name": item.name,
            "ic_date": item.ic_date,
            "days_until": (item.ic_date - as_of).days,
            "stage": item.stage,
        }
        for item in deals
        if item.ic_date and 0 <= (item.ic_date - as_of).days <= ic_window_days
    ]
    upcoming_ic.sort(key=lambda item: (item["ic_date"], item["code"]))

    deal_by_id = {item.id: item for item in deals}
    overdue_tasks = [
        {
            "task_id": item.id,
            "deal_id": item.deal_id,
            "deal_code": deal_by_id[item.deal_id].code,
            "title": item.title,
            "assignee_actor_id": item.assignee_actor_id,
            "priority": item.priority,
            "status": item.status,
            "due_date": item.due_date,
            "days_overdue": (as_of - item.due_date).days,
        }
        for item in tasks
        if item.due_date and item.due_date < as_of and item.status not in _TERMINAL_TASKS
    ]
    overdue_tasks.sort(key=lambda item: (-item["days_overdue"], item["deal_code"]))

    workstream_health: list[dict[str, Any]] = []
    for deal in deals:
        rows = streams_by_deal[deal.id]
        blocked = sum(item.status == "blocked" for item in rows)
        late = sum(
            bool(item.due_date and item.due_date < as_of and item.status not in {"complete", "waived"})
            for item in rows
        )
        if blocked:
            health = "blocked"
        elif late:
            health = "late"
        elif rows and all(item.status in {"complete", "waived"} for item in rows):
            health = "complete"
        elif rows:
            health = "in_progress"
        else:
            health = "not_configured"
        workstream_health.append(
            {
                "deal_id": deal.id,
                "deal_code": deal.code,
                "total": len(rows),
                "complete": sum(item.status in {"complete", "waived"} for item in rows),
                "in_progress": sum(item.status == "in_progress" for item in rows),
                "blocked": blocked,
                "late": late,
                "health": health,
            }
        )

    diligence_sla: list[dict[str, Any]] = []
    for item in requests:
        if item.status in _TERMINAL_REQUESTS:
            continue
        started = item.requested_at or item.created_at
        days_overdue = max(0, (as_of - item.due_date).days) if item.due_date else 0
        sla_status = "overdue" if days_overdue else "due_soon" if item.due_date and (item.due_date - as_of).days <= 3 else "on_track"
        diligence_sla.append(
            {
                "request_id": item.id,
                "deal_id": item.deal_id,
                "deal_code": deal_by_id[item.deal_id].code,
                "request_number": item.request_number,
                "title": item.title,
                "status": item.status,
                "priority": item.priority,
                "owner_actor_id": item.owner_actor_id,
                "due_date": item.due_date,
                "age_days": _age_days(started, as_of),
                "days_overdue": days_overdue,
                "sla_status": sla_status,
            }
        )
    diligence_sla.sort(key=lambda item: (-item["days_overdue"], -item["age_days"]))

    critical_risks = [
        {
            "entry_id": item.id,
            "deal_id": item.deal_id,
            "deal_code": deal_by_id[item.deal_id].code,
            "title": item.title,
            "severity": item.severity,
            "status": item.status,
            "owner_actor_id": item.owner_actor_id,
            "evidence_refs": item.evidence_refs,
            "age_days": _age_days(item.created_at, as_of),
        }
        for item in ledger
        if item.entry_type == "risk"
        and item.severity in {"high", "critical"}
        and item.status not in _CLOSED_RISKS
    ]
    critical_risks.sort(
        key=lambda item: (item["severity"] != "critical", -item["age_days"])
    )

    condition_rows = [
        {
            "condition_id": item.id,
            "deal_id": item.deal_id,
            "deal_code": deal_by_id[item.deal_id].code,
            "description": item.description,
            "owner_actor_id": item.owner_actor_id,
            "due_date": item.due_date,
            "status": item.status,
            "days_overdue": max(0, (as_of - item.due_date).days) if item.due_date else 0,
        }
        for item in conditions
        if item.status == "open"
    ]
    condition_rows.sort(key=lambda item: (-item["days_overdue"], item["deal_code"]))

    workload: dict[str, dict[str, Any]] = {}
    for item in tasks:
        if item.status in _TERMINAL_TASKS:
            continue
        actor_id = item.assignee_actor_id or "unassigned"
        row = workload.setdefault(
            actor_id,
            {"actor_id": actor_id, "open_tasks": 0, "overdue_tasks": 0, "critical_tasks": 0, "deal_ids": set()},
        )
        row["open_tasks"] += 1
        row["overdue_tasks"] += int(bool(item.due_date and item.due_date < as_of))
        row["critical_tasks"] += int(item.priority == "critical")
        row["deal_ids"].add(item.deal_id)
    team_workload = [
        {**{key: value for key, value in row.items() if key != "deal_ids"}, "deals": len(row["deal_ids"])}
        for row in workload.values()
    ]
    team_workload.sort(key=lambda item: (-item["overdue_tasks"], -item["open_tasks"], item["actor_id"]))

    returns_snapshots: list[dict[str, Any]] = []
    downside_watchlist: list[dict[str, Any]] = []
    covenant_watchlist: list[dict[str, Any]] = []
    for deal in deals:
        rows = sorted(cases_by_workspace[deal.workspace_id or ""], key=lambda item: item.case_key)
        if rows:
            returns_snapshots.append(
                {
                    "deal_id": deal.id,
                    "deal_code": deal.code,
                    "cases": [_case_payload(item) for item in rows],
                }
            )
        for item in rows:
            payload = _case_payload(item)
            if item.case_key == "downside":
                checks = (
                    ("xirr", payload["xirr"], 0.15, "Downside IRR is below 15%"),
                    ("moic", payload["moic"], 1.5, "Downside MOIC is below 1.5x"),
                    ("minimum_liquidity", payload["minimum_liquidity"], 0.0, "Downside liquidity falls below zero"),
                )
                for metric, value, threshold, reason in checks:
                    if value is not None and value < threshold:
                        downside_watchlist.append(
                            {
                                "deal_id": deal.id,
                                "deal_code": deal.code,
                                "case_key": item.case_key,
                                "reason": reason,
                                "severity": "critical" if metric == "minimum_liquidity" and value < 0 else "high",
                                "metric": metric,
                                "value": value,
                            }
                        )
            if payload["first_covenant_breach"]:
                covenant_watchlist.append(
                    {
                        "deal_id": deal.id,
                        "deal_code": deal.code,
                        "case_key": item.case_key,
                        "reason": "Projected covenant breach",
                        "severity": "high",
                        "metric": "first_covenant_breach",
                        "value": payload["first_covenant_breach"],
                    }
                )
            if payload["first_debt_service_default"]:
                covenant_watchlist.append(
                    {
                        "deal_id": deal.id,
                        "deal_code": deal.code,
                        "case_key": item.case_key,
                        "reason": "Projected debt-service default",
                        "severity": "critical",
                        "metric": "first_debt_service_default",
                        "value": payload["first_debt_service_default"],
                    }
                )

    deal_for_workspace = {item.workspace_id: item for item in deals if item.workspace_id}
    exception_rows = [
        {
            "exception_id": item.id,
            "deal_id": deal_for_workspace[item.workspace_id].id,
            "deal_code": deal_for_workspace[item.workspace_id].code,
            "workspace_id": item.workspace_id,
            "severity": item.severity,
            "code": item.code,
            "message": item.message,
            "state": item.state,
            "age_days": _age_days(item.created_at, as_of),
        }
        for item in import_exceptions
        if item.state == "open" and item.workspace_id in deal_for_workspace
    ]
    exception_rows.sort(key=lambda item: (-item["age_days"], item["deal_code"]))

    active_deals = sum(item.status in {"active", "on_hold"} for item in deals)
    return {
        "organization_id": organization_id,
        "generated_at": now_utc(),
        "filters": {
            "search": search,
            "stage": stage,
            "fund_id": fund_id,
            "as_of": as_of,
            "ic_window_days": ic_window_days,
        },
        "headline": {
            "deals": len(deals),
            "active_deals": active_deals,
            "funds": len(funds),
            "at_ic": sum(item.stage == "ic_review" for item in deals),
            "ic_next_30_days": len(upcoming_ic),
            "overdue_tasks": len(overdue_tasks),
            "critical_risks": len(critical_risks),
            "open_conditions": len(condition_rows),
            "average_readiness": round(sum(readiness_values) / len(readiness_values), 1) if readiness_values else 0.0,
        },
        "stage_funnel": [
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "count": sum(item.stage == key for item in deals),
                "percent": round(sum(item.stage == key for item in deals) / len(deals) * 100, 1) if deals else 0.0,
            }
            for key in _STAGE_ORDER
        ],
        "sector_exposure": _distribution(
            (row["sector"] for row in deal_rows), len(deal_rows)
        ),
        "strategy_exposure": _distribution(
            (row["strategy"] for row in deal_rows), len(deal_rows)
        ),
        "deals": deal_rows,
        "upcoming_ic": upcoming_ic,
        "overdue_tasks": overdue_tasks,
        "workstream_health": workstream_health,
        "diligence_sla": diligence_sla,
        "critical_risks": critical_risks,
        "conditions_to_close": condition_rows,
        "team_workload": team_workload,
        "returns_snapshots": returns_snapshots,
        "downside_watchlist": downside_watchlist,
        "covenant_watchlist": covenant_watchlist,
        "import_exceptions": exception_rows,
    }


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """Neutralize spreadsheet formula injection (CWE-1236).

    Deal/target names and sectors are user-controlled free text. A value beginning with a
    formula trigger (``=``, ``+``, ``-``, ``@``, tab, CR) is prefixed with a leading apostrophe
    so Excel/Sheets render it as literal text instead of executing e.g. ``=WEBSERVICE(...)``.
    Non-string cells (numbers, dates) pass through unchanged.
    """
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


def export_dashboard_csv(dashboard: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    fields = (
        "code",
        "name",
        "target_company",
        "fund_name",
        "strategy",
        "sector",
        "stage",
        "status",
        "ic_date",
        "stage_age_days",
        "readiness_score",
        "source_status",
        "mapping_coverage",
        "reconciliation_score",
        "open_exceptions",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for deal in dashboard["deals"]:
        writer.writerow(
            {
                # User-controlled free-text fields are formula-neutralized on export.
                "code": _csv_safe(deal["code"]),
                "name": _csv_safe(deal["name"]),
                "target_company": _csv_safe(deal["target_company"]),
                "fund_name": _csv_safe(deal["fund_name"]),
                "strategy": _csv_safe(deal["strategy"]),
                "sector": _csv_safe(deal["sector"]),
                "stage": deal["stage"],
                "status": deal["status"],
                "ic_date": deal["ic_date"].isoformat() if deal["ic_date"] else "",
                "stage_age_days": deal["stage_age_days"],
                "readiness_score": deal["readiness_score"],
                "source_status": deal["source_health"]["status"],
                "mapping_coverage": deal["financial_quality"]["mapping_coverage"],
                "reconciliation_score": deal["financial_quality"]["reconciliation_score"],
                "open_exceptions": deal["financial_quality"]["open_exceptions"],
            }
        )
    return output.getvalue()


# --- Fund-level portfolio construction (G29) ------------------------------------------------

# Default concentration limits, expressed as fractions of a fund's sized (deployed) capital.
# Every limit is overridable per request; nothing here is imputed onto missing data.
DEFAULT_CONCENTRATION_LIMITS: dict[str, float] = {
    "single_sector_max": 0.30,
    "single_deal_max": 0.15,
    "single_strategy_max": 0.50,
}
DEFAULT_NEAR_BREACH_RATIO = 0.90
DEFAULT_INVESTMENT_PERIOD_YEARS = 5
DEFAULT_PACING_TOLERANCE = 0.10
# Sizing is read from the fund's committed sponsor equity in a deal's underwriting case. The base
# case is canonical; if it is absent we fall back to the lexically first case key so the figure is
# deterministic. A deal with no case (or no sources & uses) is treated as UNSIZED, never imputed.
_SIZING_CASE_PREFERENCE = "base"


def _committed_capital(case: UnderwritingCaseVersion | None) -> float | None:
    """Fund equity deployed into a deal, or ``None`` when the case carries no sized sources & uses."""
    if case is None:
        return None
    sources_uses = (case.result or {}).get("sources_uses") or {}
    return _number(sources_uses.get("sponsor_equity"))


def _pick_sizing_case(cases: list[UnderwritingCaseVersion]) -> UnderwritingCaseVersion | None:
    if not cases:
        return None
    by_key = {item.case_key: item for item in _latest_cases(cases)}
    if _SIZING_CASE_PREFERENCE in by_key:
        return by_key[_SIZING_CASE_PREFERENCE]
    return by_key[min(by_key)]


def _exposure_buckets(
    sized: list[dict[str, Any]], dimension_key: str, deployed: float
) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    for row in sized:
        totals[row[dimension_key]] += row["committed"]
    buckets = [
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "sized_amount": round(amount, 2),
            "exposure_pct": round(amount / deployed, 4) if deployed else 0.0,
        }
        for key, amount in totals.items()
    ]
    buckets.sort(key=lambda item: (-item["sized_amount"], item["key"]))
    return buckets


def _detect_breaches(
    buckets: list[dict[str, Any]],
    dimension: str,
    limit: float,
    near_ratio: float,
    *,
    min_buckets: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split buckets into over-limit breaches and within-``near_ratio`` near-breaches.

    ``min_buckets`` guards structural dimensions: a fund's strategy is its mandate, so a single
    100% strategy bucket is not a concentration *risk* and only fires when >1 strategy is present.
    """
    breaches: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    if len(buckets) < min_buckets:
        return breaches, near
    for bucket in buckets:
        pct = bucket["exposure_pct"]
        if pct > limit:
            breaches.append(
                {
                    "dimension": dimension,
                    "key": bucket["key"],
                    "exposure_pct": pct,
                    "limit": limit,
                    "excess": round(pct - limit, 4),
                }
            )
        elif pct >= limit * near_ratio:
            near.append(
                {
                    "dimension": dimension,
                    "key": bucket["key"],
                    "exposure_pct": pct,
                    "limit": limit,
                    "headroom": round(limit - pct, 4),
                }
            )
    return breaches, near


def _pacing(
    fund: Fund,
    deployed: float,
    target: float | None,
    as_of: date,
    investment_period_years: int,
    tolerance: float,
) -> dict[str, Any]:
    """Linear-deployment pacing: expected fraction deployed by ``as_of`` given the vintage year."""
    vintage = fund.vintage_year
    years_elapsed: float | None = None
    expected_pct: float | None = None
    if vintage is not None:
        elapsed_days = (as_of - date(vintage, 1, 1)).days
        years_elapsed = round(max(0.0, elapsed_days / 365.25), 3)
        if investment_period_years > 0:
            expected_pct = round(min(years_elapsed / investment_period_years, 1.0), 4)
    actual_pct = round(deployed / target, 4) if target and target > 0 else None
    if expected_pct is None or actual_pct is None:
        status = "unknown"
    elif actual_pct > expected_pct + tolerance:
        status = "ahead"
    elif actual_pct < expected_pct - tolerance:
        status = "behind"
    else:
        status = "on_track"
    return {
        "vintage_year": vintage,
        "investment_period_years": investment_period_years,
        "years_elapsed": years_elapsed,
        "expected_pct": expected_pct,
        "actual_pct": actual_pct,
        "status": status,
        "tolerance": tolerance,
    }


def get_fund_construction(
    session: Session,
    organization_id: str,
    *,
    fund_id: str | None = None,
    as_of: date | None = None,
    single_sector_max: float | None = None,
    single_deal_max: float | None = None,
    single_strategy_max: float | None = None,
    near_breach_ratio: float = DEFAULT_NEAR_BREACH_RATIO,
    target_fund_size: float | None = None,
    investment_period_years: int = DEFAULT_INVESTMENT_PERIOD_YEARS,
    pacing_tolerance: float = DEFAULT_PACING_TOLERANCE,
) -> dict[str, Any]:
    """Aggregate fund exposure vs. concentration limits and a vintage-based pacing model.

    Sizing is never imputed: a deal only contributes to deployed capital and sized exposure when its
    underwriting case carries committed sponsor equity, and ``sizing_coverage`` reports the share of
    the fund's deals that were sized.
    """
    if session.get(Organization, organization_id) is None:
        raise PortfolioError("Organization not found", 404)
    as_of = as_of or now_utc().date()
    limits = {
        "single_sector_max": DEFAULT_CONCENTRATION_LIMITS["single_sector_max"]
        if single_sector_max is None
        else single_sector_max,
        "single_deal_max": DEFAULT_CONCENTRATION_LIMITS["single_deal_max"]
        if single_deal_max is None
        else single_deal_max,
        "single_strategy_max": DEFAULT_CONCENTRATION_LIMITS["single_strategy_max"]
        if single_strategy_max is None
        else single_strategy_max,
        "near_breach_ratio": near_breach_ratio,
    }

    fund_stmt = select(Fund).where(Fund.organization_id == organization_id)
    if fund_id:
        fund_stmt = fund_stmt.where(Fund.id == fund_id)
    funds = list(session.scalars(fund_stmt))
    fund_ids = [item.id for item in funds]

    deals: list[Deal] = []
    if fund_ids:
        deal_stmt = select(Deal).where(
            Deal.organization_id == organization_id, Deal.fund_id.in_(fund_ids)
        )
        deals = list(session.scalars(deal_stmt))
    workspace_ids = [item.workspace_id for item in deals if item.workspace_id]

    targets = (
        {
            item.workspace_id: item
            for item in session.scalars(
                select(Target).where(Target.workspace_id.in_(workspace_ids))
            )
        }
        if workspace_ids
        else {}
    )
    cases_by_workspace: dict[str, list[UnderwritingCaseVersion]] = defaultdict(list)
    if workspace_ids:
        for case in session.scalars(
            select(UnderwritingCaseVersion).where(
                UnderwritingCaseVersion.workspace_id.in_(workspace_ids)
            )
        ):
            cases_by_workspace[case.workspace_id].append(case)

    deals_by_fund: dict[str, list[Deal]] = defaultdict(list)
    for deal in deals:
        deals_by_fund[deal.fund_id].append(deal)

    fund_reports: list[dict[str, Any]] = []
    for fund in sorted(
        funds, key=lambda item: (item.vintage_year is None, item.vintage_year or 0, item.name)
    ):
        fund_deals = deals_by_fund.get(fund.id, [])
        sized: list[dict[str, Any]] = []
        deal_buckets: list[dict[str, Any]] = []
        unsized_codes: list[str] = []
        for deal in fund_deals:
            case = _pick_sizing_case(cases_by_workspace.get(deal.workspace_id or "", []))
            committed = _committed_capital(case)
            if committed is None or committed <= 0:
                unsized_codes.append(deal.code)
                continue
            target = targets.get(deal.workspace_id or "")
            sector = target.sector if target and target.sector else "Unclassified"
            sized.append(
                {
                    "code": deal.code,
                    "committed": committed,
                    "sector": sector,
                    "strategy": fund.strategy,
                    "stage": deal.stage,
                }
            )
        deployed = round(sum(row["committed"] for row in sized), 2)
        for row in sorted(sized, key=lambda item: (-item["committed"], item["code"])):
            deal_buckets.append(
                {
                    "key": row["code"],
                    "label": row["code"],
                    "sized_amount": round(row["committed"], 2),
                    "exposure_pct": round(row["committed"] / deployed, 4) if deployed else 0.0,
                }
            )

        sector_buckets = _exposure_buckets(sized, "sector", deployed)
        strategy_buckets = _exposure_buckets(sized, "strategy", deployed)
        stage_buckets = _exposure_buckets(sized, "stage", deployed)

        breaches: list[dict[str, Any]] = []
        near_breaches: list[dict[str, Any]] = []
        for buckets, dimension, limit, min_buckets in (
            (sector_buckets, "sector", limits["single_sector_max"], 1),
            (deal_buckets, "deal", limits["single_deal_max"], 1),
            (strategy_buckets, "strategy", limits["single_strategy_max"], 2),
        ):
            found, near = _detect_breaches(
                buckets, dimension, limit, near_breach_ratio, min_buckets=min_buckets
            )
            breaches.extend(found)
            near_breaches.extend(near)
        breaches.sort(key=lambda item: (-item["excess"], item["dimension"], item["key"]))
        near_breaches.sort(key=lambda item: (-item["exposure_pct"], item["dimension"], item["key"]))

        total_deals = len(fund_deals)
        sized_count = len(sized)
        fund_reports.append(
            {
                "fund_id": fund.id,
                "name": fund.name,
                "vintage_year": fund.vintage_year,
                "strategy": fund.strategy,
                "base_currency": fund.base_currency,
                "deployed": deployed,
                "target": target_fund_size,
                "pacing": _pacing(
                    fund,
                    deployed,
                    target_fund_size,
                    as_of,
                    investment_period_years,
                    pacing_tolerance,
                ),
                "exposures": {
                    "sector": sector_buckets,
                    "strategy": strategy_buckets,
                    "stage": stage_buckets,
                },
                "concentration_breaches": breaches,
                "near_breaches": near_breaches,
                "sizing_coverage": {
                    "total_deals": total_deals,
                    "sized_deals": sized_count,
                    "unsized_deals": total_deals - sized_count,
                    "coverage_pct": round(sized_count / total_deals * 100, 1) if total_deals else 0.0,
                    "deployed": deployed,
                    "unsized_deal_codes": sorted(unsized_codes),
                },
            }
        )

    return {
        "organization_id": organization_id,
        "generated_at": now_utc(),
        "as_of": as_of,
        "limits": limits,
        "funds": fund_reports,
    }


def get_health(session: Session, organization_id: str) -> dict[str, Any]:
    dashboard = get_dashboard(session, organization_id)
    source_statuses = Counter(item["source_health"]["status"] for item in dashboard["deals"])
    return {
        "organization_id": organization_id,
        "generated_at": now_utc(),
        "api": "ok",
        "database": session.bind.dialect.name if session.bind else "unknown",
        "sources": dict(source_statuses),
        "stale_workspaces": sum(item["source_health"]["stale"] for item in dashboard["deals"]),
        "failed_sources": sum(item["source_health"]["failed"] for item in dashboard["deals"]),
        "partial_sources": sum(item["source_health"]["partial"] for item in dashboard["deals"]),
        "open_import_exceptions": len(dashboard["import_exceptions"]),
        "workspaces_without_sources": source_statuses["not_configured"],
    }


__all__ = [
    "PortfolioError",
    "export_dashboard_csv",
    "get_dashboard",
    "get_fund_construction",
    "get_health",
]
