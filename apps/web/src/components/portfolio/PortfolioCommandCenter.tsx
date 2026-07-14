"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { DEMO_ACTORS, useActor } from "@/components/identity/ActorContext";
import { Badge } from "@/components/ui/Badge";
import type { BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  EmptyPanel,
  Field,
  InlineError,
  SelectInput,
  TextInput,
} from "@/components/workbench/Primitives";
import { api, ApiError } from "@/lib/api";
import type {
  DealStage,
  Fund,
  Organization,
  PortfolioDashboard,
  PortfolioDealRow,
  PortfolioDistributionPoint,
  PortfolioQuery,
  PortfolioReturnCase,
  PortfolioWatchlistItem,
} from "@/lib/types";
import {
  buildFinancialRollup,
  buildSourceRollup,
  getReturnCase,
  readinessBand,
} from "./portfolioMetrics";

const STAGES: DealStage[] = [
  "sourcing",
  "screening",
  "initial_review",
  "diligence",
  "ic_review",
  "signing",
  "closed",
  "declined",
];

interface DraftFilters {
  search: string;
  stage: "" | DealStage;
  fundId: string;
  asOf: string;
  icWindowDays: string;
}

const EMPTY_FILTERS: DraftFilters = {
  search: "",
  stage: "",
  fundId: "",
  asOf: "",
  icWindowDays: "30",
};

const DATE = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const DATE_TIME = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const COMPACT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function label(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase())
    .replace(/\bIc\b/g, "IC")
    .replace(/\bSla\b/g, "SLA")
    .replace(/\bQoe\b/g, "QoE");
}

function dateOnly(value: string | null) {
  if (!value) return "Not set";
  return DATE.format(new Date(`${value}T00:00:00`));
}

function dateTime(value: string | null) {
  if (!value) return "Not available";
  return DATE_TIME.format(new Date(value));
}

function percent(value: number | null, decimal = false) {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${(decimal ? value * 100 : value).toFixed(1)}%`;
}

function multiple(value: number | null) {
  return value === null || !Number.isFinite(value) ? "—" : `${value.toFixed(2)}x`;
}

function compactNumber(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  return COMPACT.format(value);
}

function actorName(actorId: string | null) {
  if (!actorId) return "Unassigned";
  return DEMO_ACTORS.find((item) => item.id === actorId)?.shortName ?? actorId;
}

function severityTone(value: string): BadgeTone {
  if (value === "critical" || value === "failed") return "critical";
  if (["high", "overdue", "blocked"].includes(value)) return "red";
  if (["medium", "partial", "due_soon", "late"].includes(value)) return "amber";
  if (["ready", "complete", "on_track", "satisfied"].includes(value)) return "green";
  return "slate";
}

function sourceTone(value: string): BadgeTone {
  if (value === "ready") return "green";
  if (value === "partial") return "amber";
  if (value === "failed") return "critical";
  return "slate";
}

function ProgressBar({ value, tone = "accent" }: { value: number; tone?: "accent" | "green" | "amber" | "red" }) {
  const colors = {
    accent: "bg-accent",
    green: "bg-positive",
    amber: "bg-warn",
    red: "bg-negative",
  };
  const safeValue = Math.max(0, Math.min(100, value));
  return (
    <div
      className="h-1.5 overflow-hidden rounded-full bg-sunken"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={safeValue}
    >
      <div className={`h-full rounded-full ${colors[tone]}`} style={{ width: `${safeValue}%` }} />
    </div>
  );
}

function LoadingCommandCenter() {
  return (
    <div className="space-y-5" aria-live="polite" aria-busy="true">
      <p className="sr-only">Loading portfolio command center</p>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line md:grid-cols-4 xl:grid-cols-8">
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="h-24 animate-pulse bg-panel p-4">
            <div className="h-2.5 w-20 rounded bg-sunken" />
            <div className="mt-4 h-6 w-12 rounded bg-sunken" />
          </div>
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="h-72 animate-pulse rounded-md border border-line bg-panel" />
        <div className="h-72 animate-pulse rounded-md border border-line bg-panel" />
      </div>
    </div>
  );
}

function Kpi({ label: title, value, detail, alert = false }: { label: string; value: ReactNode; detail: string; alert?: boolean }) {
  return (
    <div className={`min-w-0 bg-panel px-4 py-3.5 ${alert ? "shadow-[inset_0_2px_0_#A23A2E]" : ""}`}>
      <div className="text-2xs font-semibold uppercase tracking-eyebrow text-muted">{title}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${alert ? "text-negative" : "text-ink"}`}>{value}</div>
      <div className="mt-1 truncate text-2xs text-faint">{detail}</div>
    </div>
  );
}

function HeadlineStrip({ dashboard }: { dashboard: PortfolioDashboard }) {
  const headline = dashboard.headline;
  return (
    <section aria-label="Portfolio headline metrics" className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line md:grid-cols-4 xl:grid-cols-8">
      <Kpi label="Active deals" value={headline.active_deals} detail={`${headline.deals} in current view`} />
      <Kpi label="Funds" value={headline.funds} detail="Represented in view" />
      <Kpi label="Average readiness" value={headline.deals ? percent(headline.average_readiness) : "—"} detail="Weighted control score" />
      <Kpi label="At IC" value={headline.at_ic} detail="IC review stage" />
      <Kpi label="IC upcoming" value={headline.ic_next_30_days} detail={`${dashboard.filters.ic_window_days}-day window`} />
      <Kpi label="Overdue tasks" value={headline.overdue_tasks} detail="Open past due" alert={headline.overdue_tasks > 0} />
      <Kpi label="Critical risks" value={headline.critical_risks} detail="High or critical open" alert={headline.critical_risks > 0} />
      <Kpi label="Conditions" value={headline.open_conditions} detail="Open to close" alert={headline.open_conditions > 0} />
    </section>
  );
}

function DistributionBars({ points, emptyText }: { points: PortfolioDistributionPoint[]; emptyText: string }) {
  const visible = points.filter((item) => item.count > 0);
  if (!visible.length) return <p className="py-12 text-center text-xs text-muted">{emptyText}</p>;
  return (
    <div className="space-y-3.5">
      {visible.map((item) => (
        <div key={item.key}>
          <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
            <span className="font-medium text-body">{item.label}</span>
            <span className="tabular-nums text-muted">{item.count} <span className="text-faint">· {percent(item.percent)}</span></span>
          </div>
          <ProgressBar value={item.percent} />
        </div>
      ))}
    </div>
  );
}

function StageFunnel({ points }: { points: PortfolioDistributionPoint[] }) {
  const max = Math.max(...points.map((item) => item.count), 1);
  return (
    <div className="space-y-2">
      {points.map((item) => (
        <div key={item.key} className="grid grid-cols-[7rem_1fr_2.5rem] items-center gap-3">
          <span className="truncate text-2xs font-semibold uppercase tracking-wide text-muted">{item.label}</span>
          <div className="h-5 overflow-hidden rounded-sm bg-panel2">
            {item.count > 0 && (
              <div
                className="flex h-full min-w-[1.75rem] items-center justify-end bg-accent/85 px-1.5 text-[10px] font-semibold text-white"
                style={{ width: `${Math.max(8, (item.count / max) * 100)}%` }}
              >
                {percent(item.percent)}
              </div>
            )}
          </div>
          <span className="text-right text-xs font-semibold tabular-nums text-ink">{item.count}</span>
        </div>
      ))}
    </div>
  );
}

function PortfolioShape({ dashboard }: { dashboard: PortfolioDashboard }) {
  return (
    <div className="grid gap-5 xl:grid-cols-[1.1fr_.9fr]">
      <Card eyebrow="Pipeline conversion" title="Stage funnel" subtitle="Current deals by investment-process stage">
        <StageFunnel points={dashboard.stage_funnel} />
      </Card>
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
        <Card eyebrow="Exposure" title="Sector mix" bodyClassName="px-5 py-4">
          <DistributionBars points={dashboard.sector_exposure} emptyText="No sector classifications in this view." />
        </Card>
        <Card eyebrow="Exposure" title="Strategy mix" bodyClassName="px-5 py-4">
          <DistributionBars points={dashboard.strategy_exposure} emptyText="No fund strategies in this view." />
        </Card>
      </div>
    </div>
  );
}

function DealDetail({ deal }: { deal: PortfolioDealRow }) {
  const quality = deal.financial_quality;
  return (
    <div className="grid gap-5 bg-panel2 p-5 lg:grid-cols-[1.35fr_.65fr]">
      <div>
        <h4 className="font-sans text-xs font-semibold uppercase tracking-eyebrow text-ink">Readiness controls</h4>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {deal.readiness_components.map((component) => (
            <div key={component.key} className="rounded border border-line bg-panel p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-ink">{component.label}</span>
                <span className="text-2xs tabular-nums text-muted">{percent(component.score)}</span>
              </div>
              <div className="mt-2"><ProgressBar value={component.score} /></div>
              <p className="mt-2 text-2xs leading-relaxed text-muted">{component.explanation}</p>
              <p className="mt-1 text-[10px] uppercase tracking-wide text-faint">{component.passed}/{component.total} passed · {percent(component.weight, true)} weight</p>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h4 className="font-sans text-xs font-semibold uppercase tracking-eyebrow text-ink">Financial data quality</h4>
        <dl className="mt-3 divide-y divide-line rounded border border-line bg-panel px-3">
          <DetailMetric label="Account mapping" value={percent(quality.mapping_coverage)} detail={`${quality.mapped_facts}/${quality.total_facts} facts`} />
          <DetailMetric label="Reconciliations" value={percent(quality.reconciliation_score)} detail={`${quality.reconciliations_passed}/${quality.reconciliations_total} passed`} />
          <DetailMetric label="Open exceptions" value={quality.open_exceptions} />
          <DetailMetric label="QoE adjustments" value={compactNumber(quality.qoe_adjustment_amount)} detail={quality.qoe_materiality === null ? "Materiality unavailable" : `${percent(quality.qoe_materiality, true)} of reported EBITDA`} />
          <DetailMetric label="EBITDA variance" value={compactNumber(quality.ebitda_variance)} detail="Sponsor less reported" />
          <DetailMetric label="Period consistency" value={quality.period_consistent === null ? "Not tested" : quality.period_consistent ? "Consistent" : "Review"} />
        </dl>
        {quality.period_diagnostics.length > 0 && (
          <ul className="mt-3 space-y-1.5 rounded border border-[#ead8b9] bg-[#fbf7ef] p-3 text-2xs leading-relaxed text-severity-medium">
            {quality.period_diagnostics.map((item) => <li key={item}>• {item}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

function DetailMetric({ label: title, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <dt className="text-2xs font-medium text-muted">{title}</dt>
      <dd className="text-right text-xs font-semibold tabular-nums text-ink">{value}{detail && <span className="ml-1.5 font-normal text-faint">{detail}</span>}</dd>
    </div>
  );
}

function DealTable({ deals }: { deals: PortfolioDealRow[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  return (
    <Card
      eyebrow="Deal-level control"
      title="Portfolio register"
      subtitle="Readiness is a weighted operating-control score; expand a deal to inspect its components."
      right={<span className="text-2xs text-muted">{deals.length} deal{deals.length === 1 ? "" : "s"}</span>}
      bodyClassName="p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1080px] border-collapse text-left text-xs">
          <thead className="bg-panel2 text-2xs uppercase tracking-eyebrow text-muted">
            <tr>
              <th className="px-4 py-3 font-semibold">Deal</th>
              <th className="px-3 py-3 font-semibold">Fund / strategy</th>
              <th className="px-3 py-3 font-semibold">Stage</th>
              <th className="px-3 py-3 font-semibold">IC date</th>
              <th className="px-3 py-3 font-semibold">Readiness</th>
              <th className="px-3 py-3 font-semibold">Sources</th>
              <th className="px-3 py-3 font-semibold">Mapping</th>
              <th className="px-4 py-3 text-right font-semibold">Workspace</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {deals.map((deal) => {
              const band = readinessBand(deal.readiness_score);
              const isExpanded = expanded === deal.id;
              return (
                <Fragment key={deal.id}>
                  <tr className="bg-panel align-middle transition hover:bg-panel2/70">
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        className="group flex max-w-[18rem] items-start gap-2 text-left"
                        aria-expanded={isExpanded}
                        onClick={() => setExpanded(isExpanded ? null : deal.id)}
                      >
                        <span className="mt-0.5 text-[10px] text-faint transition group-hover:text-accent" aria-hidden>{isExpanded ? "▼" : "▶"}</span>
                        <span>
                          <span className="block font-mono text-2xs font-semibold text-accent">{deal.code}</span>
                          <span className="mt-0.5 block truncate font-semibold text-ink">{deal.target_company}</span>
                          <span className="mt-0.5 block text-2xs text-faint">{deal.sector}</span>
                        </span>
                      </button>
                    </td>
                    <td className="px-3 py-3"><span className="block font-medium text-body">{deal.fund_name}</span><span className="mt-0.5 block text-2xs text-faint">{label(deal.strategy)}</span></td>
                    <td className="px-3 py-3"><Badge tone={deal.stage === "ic_review" ? "gold" : "slate"}>{label(deal.stage)}</Badge><span className="mt-1.5 block text-2xs text-faint">{deal.stage_age_days}d in stage</span></td>
                    <td className="px-3 py-3 text-body">{dateOnly(deal.ic_date)}</td>
                    <td className="w-40 px-3 py-3"><div className="flex items-center justify-between gap-2"><span className="font-semibold tabular-nums text-ink">{percent(deal.readiness_score)}</span><Badge tone={band.tone}>{band.label}</Badge></div><div className="mt-2"><ProgressBar value={deal.readiness_score} tone={deal.readiness_score >= 80 ? "green" : deal.readiness_score < 40 ? "red" : "accent"} /></div></td>
                    <td className="px-3 py-3"><Badge tone={sourceTone(deal.source_health.status)}>{label(deal.source_health.status)}</Badge><span className="mt-1.5 block text-2xs text-faint">{deal.source_health.total_sources} registered</span></td>
                    <td className="px-3 py-3"><span className="font-semibold tabular-nums text-ink">{percent(deal.financial_quality.mapping_coverage)}</span><span className="mt-1 block text-2xs text-faint">{deal.financial_quality.open_exceptions} exceptions</span></td>
                    <td className="px-4 py-3 text-right">{deal.workspace_id ? <Link href={`/workspaces/${deal.workspace_id}`} className="font-semibold text-accent hover:underline">Open deal →</Link> : <span className="text-faint">Not connected</span>}</td>
                  </tr>
                  {isExpanded && <tr><td colSpan={8} className="p-0"><DealDetail deal={deal} /></td></tr>}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function QueuePanel({ eyebrow, title, count, empty, children }: { eyebrow: string; title: string; count: number; empty: string; children: ReactNode }) {
  return (
    <Card eyebrow={eyebrow} title={title} right={<span className={`rounded-full px-2 py-0.5 text-2xs font-semibold tabular-nums ${count ? "bg-[#f3e4e4] text-severity-critical" : "bg-sunken text-muted"}`}>{count}</span>} bodyClassName="p-0">
      {count ? <div className="max-h-[22rem] divide-y divide-line overflow-y-auto">{children}</div> : <p className="px-5 py-10 text-center text-xs text-muted">{empty}</p>}
    </Card>
  );
}

function QueueRow({ code, title, meta, badge, badgeTone = "slate" }: { code: string; title: string; meta: ReactNode; badge?: string; badgeTone?: BadgeTone }) {
  return (
    <div className="flex items-start justify-between gap-4 px-4 py-3">
      <div className="min-w-0"><div className="font-mono text-[10px] font-semibold uppercase tracking-wide text-accent">{code}</div><p className="mt-0.5 text-xs font-medium leading-snug text-ink">{title}</p><div className="mt-1 text-2xs leading-relaxed text-muted">{meta}</div></div>
      {badge && <Badge tone={badgeTone} className="shrink-0">{badge}</Badge>}
    </div>
  );
}

function ExecutionQueues({ dashboard }: { dashboard: PortfolioDashboard }) {
  return (
    <div className="grid gap-5 lg:grid-cols-2 2xl:grid-cols-4">
      <QueuePanel eyebrow="Task control" title="Overdue tasks" count={dashboard.overdue_tasks.length} empty="No open tasks are past due.">
        {dashboard.overdue_tasks.map((item) => <QueueRow key={item.task_id} code={item.deal_code} title={item.title} badge={`${item.days_overdue}d late`} badgeTone="red" meta={<>{actorName(item.assignee_actor_id)} · due {dateOnly(item.due_date)} · {label(item.priority)}</>} />)}
      </QueuePanel>
      <QueuePanel eyebrow="Request SLA" title="Diligence requests" count={dashboard.diligence_sla.length} empty="No open diligence requests in this view.">
        {dashboard.diligence_sla.map((item) => <QueueRow key={item.request_id} code={`${item.deal_code} · R${item.request_number}`} title={item.title} badge={label(item.sla_status)} badgeTone={severityTone(item.sla_status)} meta={<>{actorName(item.owner_actor_id)} · {item.due_date ? `due ${dateOnly(item.due_date)}` : "no due date"} · {item.age_days}d open</>} />)}
      </QueuePanel>
      <QueuePanel eyebrow="Risk register" title="Material risks" count={dashboard.critical_risks.length} empty="No open high or critical risks.">
        {dashboard.critical_risks.map((item) => <QueueRow key={item.entry_id} code={item.deal_code} title={item.title} badge={item.severity} badgeTone={severityTone(item.severity)} meta={<>{actorName(item.owner_actor_id)} · {item.age_days}d open · {item.evidence_refs.length} evidence ref{item.evidence_refs.length === 1 ? "" : "s"}</>} />)}
      </QueuePanel>
      <QueuePanel eyebrow="Closing control" title="Conditions to close" count={dashboard.conditions_to_close.length} empty="No open closing conditions.">
        {dashboard.conditions_to_close.map((item) => <QueueRow key={item.condition_id} code={item.deal_code} title={item.description} badge={item.days_overdue ? `${item.days_overdue}d late` : "open"} badgeTone={item.days_overdue ? "red" : "amber"} meta={<>{actorName(item.owner_actor_id)} · {item.due_date ? `due ${dateOnly(item.due_date)}` : "no due date"}</>} />)}
      </QueuePanel>
    </div>
  );
}

function ICCalendar({ dashboard }: { dashboard: PortfolioDashboard }) {
  return (
    <Card eyebrow="Committee calendar" title={`Upcoming IC · ${dashboard.filters.ic_window_days} days`} subtitle="Scheduled meetings inside the selected forward window" bodyClassName="p-0">
      {dashboard.upcoming_ic.length ? (
        <div className="grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-3 xl:grid-cols-4">
          {dashboard.upcoming_ic.map((item) => (
            <div key={item.deal_id} className="flex min-h-28 gap-3 p-4">
              <div className="w-12 shrink-0 border-r border-line pr-3 text-center"><div className="text-2xs font-semibold uppercase text-muted">{new Date(`${item.ic_date}T00:00:00`).toLocaleDateString("en-US", { month: "short" })}</div><div className="font-serif text-2xl font-semibold text-ink">{new Date(`${item.ic_date}T00:00:00`).getDate()}</div></div>
              <div className="min-w-0"><div className="font-mono text-[10px] font-semibold text-accent">{item.code}</div><p className="mt-1 truncate text-xs font-semibold text-ink">{item.name}</p><div className="mt-2 flex flex-wrap gap-1.5"><Badge tone="gold">{label(item.stage)}</Badge><Badge tone="slate">{item.days_until === 0 ? "Today" : `${item.days_until}d`}</Badge></div></div>
            </div>
          ))}
        </div>
      ) : <p className="px-5 py-12 text-center text-xs text-muted">No IC meetings are scheduled in this window.</p>}
    </Card>
  );
}

function OperationalHealth({ dashboard }: { dashboard: PortfolioDashboard }) {
  const configured = dashboard.workstream_health.filter((item) => item.total > 0);
  const maxTasks = Math.max(...dashboard.team_workload.map((item) => item.open_tasks), 1);
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card eyebrow="Workstream control" title="Execution health" subtitle="Status by deal across configured diligence workstreams" bodyClassName="p-0">
        {configured.length ? <div className="divide-y divide-line">{configured.map((item) => (
          <div key={item.deal_id} className="grid grid-cols-[6rem_1fr_auto] items-center gap-3 px-4 py-3">
            <span className="font-mono text-2xs font-semibold text-accent">{item.deal_code}</span>
            <div><ProgressBar value={item.total ? (item.complete / item.total) * 100 : 0} tone={item.health === "blocked" ? "red" : item.health === "late" ? "amber" : "green"} /><div className="mt-1.5 text-2xs text-faint">{item.complete}/{item.total} complete · {item.blocked} blocked · {item.late} late</div></div>
            <Badge tone={severityTone(item.health)}>{label(item.health)}</Badge>
          </div>
        ))}</div> : <p className="px-5 py-12 text-center text-xs text-muted">No diligence workstreams are configured for these deals.</p>}
      </Card>
      <Card eyebrow="Capacity" title="Team workload" subtitle="Open task load by assigned actor" bodyClassName="p-0">
        {dashboard.team_workload.length ? <div className="divide-y divide-line">{dashboard.team_workload.map((item) => (
          <div key={item.actor_id} className="grid grid-cols-[7.5rem_1fr_auto] items-center gap-3 px-4 py-3">
            <div><p className="truncate text-xs font-semibold text-ink">{actorName(item.actor_id)}</p><p className="mt-0.5 text-2xs text-faint">{item.deals} deal{item.deals === 1 ? "" : "s"}</p></div>
            <div><ProgressBar value={(item.open_tasks / maxTasks) * 100} tone={item.overdue_tasks ? "amber" : "accent"} /><p className="mt-1.5 text-2xs text-faint">{item.open_tasks} open · {item.overdue_tasks} overdue</p></div>
            {item.critical_tasks ? <Badge tone="critical">{item.critical_tasks} critical</Badge> : <Badge tone="slate">clear</Badge>}
          </div>
        ))}</div> : <p className="px-5 py-12 text-center text-xs text-muted">No open tasks are assigned in this view.</p>}
      </Card>
    </div>
  );
}

function ReturnCaseCell({ item }: { item: PortfolioReturnCase | null }) {
  if (!item) return <div className="text-center text-faint">Not modeled</div>;
  return (
    <div className="min-w-32">
      <div className="flex items-baseline justify-between gap-2"><span className="text-sm font-semibold tabular-nums text-ink">{percent(item.xirr, true)}</span><span className="text-xs font-medium tabular-nums text-body">{multiple(item.moic)}</span></div>
      <div className="mt-1.5 flex justify-between gap-2 text-[10px] uppercase tracking-wide text-faint"><span>IRR</span><span>MOIC</span></div>
      <div className="mt-2 border-t border-line pt-1.5 text-2xs text-muted">Min. liquidity <span className="float-right tabular-nums text-body">{compactNumber(item.minimum_liquidity)}</span></div>
    </div>
  );
}

function Watchlist({ title, items, empty }: { title: string; items: PortfolioWatchlistItem[]; empty: string }) {
  return (
    <Card eyebrow="Exception watch" title={title} right={<span className="text-2xs tabular-nums text-muted">{items.length}</span>} bodyClassName="p-0">
      {items.length ? <div className="max-h-72 divide-y divide-line overflow-y-auto">{items.map((item, index) => (
        <QueueRow key={`${item.deal_id}-${item.metric}-${index}`} code={`${item.deal_code} · ${label(item.case_key)}`} title={item.reason} badge={item.severity} badgeTone={severityTone(item.severity)} meta={<>{label(item.metric)} · {typeof item.value === "number" ? item.metric === "xirr" ? percent(item.value, true) : item.metric === "moic" ? multiple(item.value) : compactNumber(item.value) : item.value ?? "Value unavailable"}</>} />
      ))}</div> : <p className="px-5 py-10 text-center text-xs text-muted">{empty}</p>}
    </Card>
  );
}

function ReturnsAndDownside({ dashboard }: { dashboard: PortfolioDashboard }) {
  return (
    <div className="space-y-5">
      <Card eyebrow="Underwriting comparison" title="Latest case returns" subtitle="Latest model version for each case; amounts are shown in each model's native units." bodyClassName="p-0">
        {dashboard.returns_snapshots.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-xs"><thead className="bg-panel2 text-2xs uppercase tracking-eyebrow text-muted"><tr><th className="px-4 py-3 font-semibold">Deal</th><th className="px-4 py-3 font-semibold">Downside</th><th className="px-4 py-3 font-semibold">Base</th><th className="px-4 py-3 font-semibold">Upside</th></tr></thead><tbody className="divide-y divide-line">{dashboard.returns_snapshots.map((snapshot) => <tr key={snapshot.deal_id} className="align-top"><td className="px-4 py-4"><span className="font-mono text-2xs font-semibold text-accent">{snapshot.deal_code}</span><span className="mt-1 block text-2xs text-faint">Latest approved or saved versions</span></td><td className="px-4 py-4"><ReturnCaseCell item={getReturnCase(snapshot, "downside")} /></td><td className="border-x border-line px-4 py-4"><ReturnCaseCell item={getReturnCase(snapshot, "base")} /></td><td className="px-4 py-4"><ReturnCaseCell item={getReturnCase(snapshot, "upside")} /></td></tr>)}</tbody></table></div> : <p className="px-5 py-12 text-center text-xs text-muted">No saved underwriting cases are available for these deals.</p>}
      </Card>
      <div className="grid gap-5 lg:grid-cols-2">
        <Watchlist title="Downside thresholds" items={dashboard.downside_watchlist} empty="No modeled downside case breaches the portfolio thresholds." />
        <Watchlist title="Covenant & debt service" items={dashboard.covenant_watchlist} empty="No projected covenant breach or debt-service default is recorded." />
      </div>
    </div>
  );
}

function DataQuality({ dashboard }: { dashboard: PortfolioDashboard }) {
  const sources = buildSourceRollup(dashboard.deals);
  const financials = buildFinancialRollup(dashboard.deals);
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line md:grid-cols-4 xl:grid-cols-8">
        <Kpi label="Registered sources" value={sources.totalSources} detail={`${sources.readySources} ready`} />
        <Kpi label="Partial sources" value={sources.partialSources} detail="Latest snapshot status" alert={sources.partialSources > 0} />
        <Kpi label="Failed sources" value={sources.failedSources} detail="Latest snapshot status" alert={sources.failedSources > 0} />
        <Kpi label="Stale workspaces" value={sources.staleWorkspaces} detail="Source older than 90d" alert={sources.staleWorkspaces > 0} />
        <Kpi label="Mapping coverage" value={percent(financials.mappingCoverage)} detail={`${financials.mappedFacts}/${financials.totalFacts} facts`} />
        <Kpi label="Reconciliation" value={percent(financials.reconciliationScore)} detail={`${financials.reconciliationsPassed}/${financials.reconciliationsTotal} passed`} />
        <Kpi label="Import exceptions" value={financials.openExceptions} detail="Open across deals" alert={financials.openExceptions > 0} />
        <Kpi label="Period issues" value={financials.inconsistentPeriods} detail={`${sources.workspacesWithoutSources} without sources`} alert={financials.inconsistentPeriods > 0} />
      </div>
      <Card eyebrow="Source registry" title="Deal data health" subtitle="Latest source state and financial normalization coverage by workspace" bodyClassName="p-0">
        <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-xs"><thead className="bg-panel2 text-2xs uppercase tracking-eyebrow text-muted"><tr><th className="px-4 py-3 font-semibold">Deal</th><th className="px-3 py-3 font-semibold">Source state</th><th className="px-3 py-3 font-semibold">Freshness</th><th className="px-3 py-3 font-semibold">Mapping</th><th className="px-3 py-3 font-semibold">Reconciliation</th><th className="px-4 py-3 text-right font-semibold">Exceptions</th></tr></thead><tbody className="divide-y divide-line">{dashboard.deals.map((deal) => <tr key={deal.id}><td className="px-4 py-3"><span className="font-mono text-2xs font-semibold text-accent">{deal.code}</span><span className="ml-2 font-medium text-ink">{deal.target_company}</span></td><td className="px-3 py-3"><Badge tone={sourceTone(deal.source_health.status)}>{label(deal.source_health.status)}</Badge><span className="ml-2 text-2xs text-faint">{deal.source_health.total_sources} sources</span></td><td className="px-3 py-3"><span className="text-body">{deal.source_health.freshest_at ? dateTime(deal.source_health.freshest_at) : "No snapshots"}</span><span className="mt-0.5 block text-2xs text-faint">{deal.source_health.oldest_age_days === null ? "Age unavailable" : `oldest ${deal.source_health.oldest_age_days}d`}</span></td><td className="px-3 py-3 font-semibold tabular-nums text-ink">{percent(deal.financial_quality.mapping_coverage)}<span className="ml-1.5 text-2xs font-normal text-faint">{deal.financial_quality.mapped_facts}/{deal.financial_quality.total_facts}</span></td><td className="px-3 py-3 font-semibold tabular-nums text-ink">{percent(deal.financial_quality.reconciliation_score)}<span className="ml-1.5 text-2xs font-normal text-faint">{deal.financial_quality.reconciliations_passed}/{deal.financial_quality.reconciliations_total}</span></td><td className="px-4 py-3 text-right"><Badge tone={deal.financial_quality.open_exceptions ? "red" : "green"}>{deal.financial_quality.open_exceptions}</Badge></td></tr>)}</tbody></table></div>
      </Card>
      <QueuePanel eyebrow="Import control" title="Open import exceptions" count={dashboard.import_exceptions.length} empty="No open financial import exceptions.">
        {dashboard.import_exceptions.map((item) => <QueueRow key={item.exception_id} code={`${item.deal_code} · ${item.code}`} title={item.message} badge={item.severity} badgeTone={severityTone(item.severity)} meta={`${item.age_days}d open · ${item.state}`} />)}
      </QueuePanel>
    </div>
  );
}

export function PortfolioCommandCenter() {
  const { actor, profile } = useActor();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [funds, setFunds] = useState<Fund[]>([]);
  const [draftFilters, setDraftFilters] = useState<DraftFilters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<PortfolioQuery>({ icWindowDays: 30 });
  const [dashboard, setDashboard] = useState<PortfolioDashboard | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setInitializing(true);
    setError(null);
    setDashboard(null);
    api.listOrganizations(actor)
      .then((items) => {
        if (!active) return;
        setOrganizations(items);
        const saved = window.localStorage.getItem("deallens.organizationId");
        const next = items.some((item) => item.id === saved) ? saved! : items[0]?.id ?? "";
        setOrganizationId(next);
      })
      .catch((caught) => {
        if (!active) return;
        setError(caught instanceof ApiError ? caught.message : "Could not load organization access.");
      })
      .finally(() => { if (active) setInitializing(false); });
    return () => { active = false; };
  }, [actor]);

  useEffect(() => {
    if (!organizationId) {
      setFunds([]);
      setDashboard(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.listFunds(organizationId, actor),
      api.getPortfolio(organizationId, appliedFilters, actor),
    ])
      .then(([fundItems, portfolio]) => {
        if (!active) return;
        setFunds(fundItems);
        setDashboard(portfolio);
      })
      .catch((caught) => {
        if (!active) return;
        setError(caught instanceof ApiError ? caught.message : "Could not load the portfolio command center.");
        setDashboard(null);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [actor, appliedFilters, organizationId, refreshNonce]);

  const selectedOrganization = organizations.find((item) => item.id === organizationId) ?? null;
  const hasFilters = Boolean(
    appliedFilters.search
    || appliedFilters.stage
    || appliedFilters.fundId
    || appliedFilters.asOf
    || appliedFilters.icWindowDays !== 30,
  );
  const generatedLabel = useMemo(() => dashboard ? dateTime(dashboard.generated_at) : null, [dashboard]);

  function chooseOrganization(id: string) {
    setOrganizationId(id);
    setFunds([]);
    setDashboard(null);
    setDraftFilters(EMPTY_FILTERS);
    setAppliedFilters({ icWindowDays: 30 });
    setExportError(null);
    if (id) window.localStorage.setItem("deallens.organizationId", id);
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAppliedFilters({
      search: draftFilters.search.trim() || undefined,
      stage: draftFilters.stage || undefined,
      fundId: draftFilters.fundId || undefined,
      asOf: draftFilters.asOf || undefined,
      icWindowDays: Number(draftFilters.icWindowDays) || 30,
    });
  }

  function clearFilters() {
    setDraftFilters(EMPTY_FILTERS);
    setAppliedFilters({ icWindowDays: 30 });
  }

  async function exportCsv() {
    if (!organizationId) return;
    setDownloading(true);
    setExportError(null);
    try {
      const result = await api.exportPortfolioCsv(organizationId, appliedFilters, actor);
      const url = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setExportError(caught instanceof ApiError ? caught.message : "Could not export the portfolio CSV.");
    } finally {
      setDownloading(false);
    }
  }

  if (initializing) return <LoadingCommandCenter />;

  if (!organizations.length) {
    return (
      <div className="space-y-4">
        <InlineError message={error} />
        <EmptyPanel title="No organization access" body="The portfolio command center needs an organization and fund-scoped deal records. Create the firm structure in Pipeline first." action={<Button href="/pipeline">Open pipeline setup</Button>} />
      </div>
    );
  }

  return (
    <div className="space-y-7">
      <form onSubmit={applyFilters} className="rounded-md border border-line bg-panel p-4 shadow-panel">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-12 lg:items-end">
          <Field label="Organization" className="sm:col-span-2 lg:col-span-3"><SelectInput value={organizationId} onChange={(event) => chooseOrganization(event.target.value)}>{organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</SelectInput></Field>
          <Field label="Search" className="lg:col-span-3"><TextInput type="search" value={draftFilters.search} onChange={(event) => setDraftFilters((current) => ({ ...current, search: event.target.value }))} placeholder="Deal, code, or target" /></Field>
          <Field label="Stage" className="lg:col-span-2"><SelectInput value={draftFilters.stage} onChange={(event) => setDraftFilters((current) => ({ ...current, stage: event.target.value as DraftFilters["stage"] }))}><option value="">All stages</option>{STAGES.map((stage) => <option key={stage} value={stage}>{label(stage)}</option>)}</SelectInput></Field>
          <Field label="Fund" className="lg:col-span-2"><SelectInput value={draftFilters.fundId} onChange={(event) => setDraftFilters((current) => ({ ...current, fundId: event.target.value }))}><option value="">All funds</option>{funds.map((fund) => <option key={fund.id} value={fund.id}>{fund.name}</option>)}</SelectInput></Field>
          <Field label="As of" className="lg:col-span-2"><TextInput type="date" value={draftFilters.asOf} onChange={(event) => setDraftFilters((current) => ({ ...current, asOf: event.target.value }))} /></Field>
          <Field label="IC window" className="lg:col-span-2"><SelectInput value={draftFilters.icWindowDays} onChange={(event) => setDraftFilters((current) => ({ ...current, icWindowDays: event.target.value }))}><option value="14">14 days</option><option value="30">30 days</option><option value="60">60 days</option><option value="90">90 days</option></SelectInput></Field>
          <div className="flex gap-2 sm:col-span-2 lg:col-span-4"><Button type="submit" disabled={loading}>Apply filters</Button><Button type="button" variant="secondary" onClick={clearFilters} disabled={loading || !hasFilters}>Clear</Button></div>
          <div className="flex items-center justify-end gap-2 sm:col-span-2 lg:col-span-6"><Button type="button" variant="secondary" onClick={() => setRefreshNonce((value) => value + 1)} disabled={loading}>Refresh</Button><Button type="button" onClick={() => void exportCsv()} disabled={loading || downloading || !dashboard}>{downloading ? "Preparing…" : "Export CSV"}</Button></div>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-2xs text-muted"><span>Viewing <strong className="text-body">{selectedOrganization?.name}</strong> as {profile.name} · {profile.roleLabel}</span><span>{generatedLabel ? `Generated ${generatedLabel}` : "Awaiting portfolio snapshot"}</span></div>
        {exportError && <div className="mt-3"><InlineError message={exportError} /></div>}
      </form>

      {error && <div className="rounded-md border border-[#e5c9c3] bg-[#fbf1ef] px-4 py-3"><InlineError message={error} /><button type="button" onClick={() => setRefreshNonce((value) => value + 1)} className="mt-2 text-xs font-semibold text-accent hover:underline">Try again</button></div>}
      {loading && !dashboard && <LoadingCommandCenter />}

      {!loading && dashboard && (
        <>
          <HeadlineStrip dashboard={dashboard} />
          {!dashboard.deals.length ? (
            <EmptyPanel
              title={hasFilters ? "No deals match these filters" : "No portfolio deals yet"}
              body={hasFilters ? "Clear or adjust the stage, fund, date, or search criteria. No zero values have been inferred for the empty result." : "Create a fund-scoped deal in Pipeline to begin monitoring readiness, execution, returns, and source quality."}
              action={hasFilters ? <Button type="button" onClick={clearFilters}>Clear filters</Button> : <Button href="/pipeline">Add pipeline deals</Button>}
            />
          ) : (
            <div className="space-y-10">
              <PortfolioShape dashboard={dashboard} />
              <DealTable deals={dashboard.deals} />
              <section className="space-y-5"><SectionMarker eyebrow="Decision cadence" title="IC calendar and execution exceptions" detail="What needs attention before the next committee decision." /><ICCalendar dashboard={dashboard} /><ExecutionQueues dashboard={dashboard} /><OperationalHealth dashboard={dashboard} /></section>
              <section className="space-y-5"><SectionMarker eyebrow="Portfolio underwriting" title="Return profile and modeled downside" detail="Latest saved cases and explicit threshold breaches; no absent case is treated as zero." /><ReturnsAndDownside dashboard={dashboard} /></section>
              <section className="space-y-5"><SectionMarker eyebrow="Data governance" title="Source and financial quality" detail="Freshness, normalization coverage, reconciliation outcomes, and open import exceptions." /><DataQuality dashboard={dashboard} /></section>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SectionMarker({ eyebrow, title, detail }: { eyebrow: string; title: string; detail: string }) {
  return <div className="border-b border-line pb-3"><div className="eyebrow">{eyebrow}</div><h2 className="mt-1 font-serif text-2xl font-semibold text-ink">{title}</h2><p className="mt-1 text-xs text-muted">{detail}</p></div>;
}
