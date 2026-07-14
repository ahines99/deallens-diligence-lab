import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/serverApi";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable } from "@/components/ui/Table";
import { EmptyPanel, Metric, MetricStrip } from "@/components/workbench/Primitives";
import { formatDate, titleCase } from "@/lib/formatting";

const money = (value: number | string | null, currency = "USD") =>
  value === null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        maximumFractionDigits: 0,
        notation: Math.abs(Number(value)) >= 1_000_000 ? "compact" : "standard",
      }).format(Number(value));
const pct = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);
const multiple = (value: number | null) => (value === null ? "—" : `${value.toFixed(1)}x`);
const tone = (status: string): BadgeTone =>
  ["ready", "complete", "approved", "accepted", "satisfied"].includes(status)
    ? "green"
    : ["failed", "blocked", "rejected", "declined"].includes(status)
      ? "red"
      : "amber";

function settledValue<T>(result: PromiseSettledResult<T>): T | null {
  return result.status === "fulfilled" ? result.value : null;
}

export default async function WorkspaceCockpit({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId: id } = await params;
  let overview;
  try {
    overview = await api.getWorkspace(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return <Callout tone="warning" title="Cannot reach the API">{error instanceof ApiError ? error.message : "Failed to load workspace."}</Callout>;
  }

  const [sourcesResult, bridgeResult, casesResult, dealResult] = await Promise.allSettled([
    api.getSources(id),
    api.getQoEBridge(id),
    api.getUnderwritingCases(id),
    api.getWorkspaceDeal(id),
  ]);
  const sources = settledValue(sourcesResult);
  const bridge = settledValue(bridgeResult);
  const cases = settledValue(casesResult);
  const deal = settledValue(dealResult);
  const dealLookupAvailable = dealResult.status === "fulfilled";
  const context = deal ? { organizationId: deal.organization_id } : {};

  const workflowResults = deal
    ? await Promise.allSettled([
        api.listTasks(deal.id, context),
        api.listDiligenceRequests(deal.id, context),
        api.listGates(deal.id, context),
        api.listLedger(deal.id, context),
        api.listICPackets(deal.id, context),
      ])
    : null;
  const tasks = workflowResults ? settledValue(workflowResults[0]) : deal ? null : [];
  const requests = workflowResults ? settledValue(workflowResults[1]) : deal ? null : [];
  const gates = workflowResults ? settledValue(workflowResults[2]) : deal ? null : [];
  const ledger = workflowResults ? settledValue(workflowResults[3]) : deal ? null : [];
  const packets = workflowResults ? settledValue(workflowResults[4]) : deal ? null : [];
  const latestPacket = packets?.[0];
  const commentsResult = latestPacket
    ? await Promise.allSettled([api.listICComments(latestPacket.id, context)])
    : null;
  const packetComments = commentsResult ? settledValue(commentsResult[0]) : latestPacket ? null : [];

  const baseCase = cases?.find((item) => item.case_key === "base");
  const openTasks = tasks == null ? null : tasks.filter((item) => !["complete", "cancelled"].includes(item.status));
  const openRequests = requests == null ? null : requests.filter((item) => !["accepted", "closed"].includes(item.status));
  const currentGates = gates == null ? null : gates.filter((item) => item.stage === deal?.stage && item.required && item.status === "pending");
  const ledgerRisks = ledger == null ? null : ledger.filter((item) => item.entry_type === "risk" && !["resolved", "accepted", "mitigated"].includes(item.status));
  const blockingComments = packetComments == null ? null : packetComments.filter((item) => item.blocking && item.status !== "resolved");
  const basePath = `/workspaces/${id}`;

  const readiness = [
    {
      label: "Underwriting data",
      href: `${basePath}/data-room`,
      status: sources === null ? "unavailable" : sources.some((item) => item.status === "ready") ? "ready" : "open",
      detail: sources === null ? "Source service unavailable" : sources.length ? `${sources.length} sealed source versions` : "Create target and import financials",
    },
    {
      label: "QoE bridge",
      href: `${basePath}/qoe`,
      status: bridgeResult.status === "rejected" ? "unavailable" : bridge?.status === "ready" ? "ready" : "open",
      detail: bridgeResult.status === "rejected" ? "QoE service unavailable" : bridge?.sponsor_ebitda != null ? `${money(bridge.sponsor_ebitda, bridge.currency ?? "USD")} normalized EBITDA` : "Map reported EBITDA and approve adjustments",
    },
    {
      label: "Three-case model",
      href: `${basePath}/underwriting`,
      status: cases === null ? "unavailable" : cases.length === 3 ? "ready" : "open",
      detail: cases === null ? "Model service unavailable" : cases.length ? `${cases.length} latest case versions` : "Build base, upside, and downside",
    },
    {
      label: "Deal execution",
      href: `${basePath}/execution`,
      status: !dealLookupAvailable ? "unavailable" : deal ? "ready" : "open",
      detail: !dealLookupAvailable ? "Workflow service unavailable" : deal ? `${titleCase(deal.stage)} · ${openTasks?.length ?? "—"} open tasks` : "Connect to an organization and fund deal",
    },
    {
      label: "Evidence review",
      href: `${basePath}/intelligence`,
      status: overview.counts.evidence > 0 ? "ready" : "open",
      detail: `${overview.counts.evidence} evidence entries`,
    },
    {
      label: "IC governance",
      href: `${basePath}/ic`,
      status: packets === null ? "unavailable" : latestPacket?.frozen_at ? "ready" : "open",
      detail: packets === null ? "IC service unavailable" : latestPacket ? `Packet v${latestPacket.version} · ${titleCase(latestPacket.status)}` : "Compose the first IC packet",
    },
  ] as const;

  const blockers = [
    { label: "Current-stage gates", value: currentGates === null ? null : currentGates.length, href: `${basePath}/execution` },
    { label: "Open ledger risks", value: ledgerRisks === null ? null : ledgerRisks.length, href: `${basePath}/execution` },
    { label: "Blocking IC comments", value: blockingComments === null ? null : blockingComments.length, href: `${basePath}/ic` },
    { label: "Failed / partial sources", value: sources === null ? null : sources.filter((item) => item.status !== "ready").length, href: `${basePath}/data-room` },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Deal cockpit"
        title={overview.target?.name ?? overview.workspace.name}
        subtitle={<span className="flex flex-wrap items-center gap-2">{deal && <Badge tone="indigo">{deal.code}</Badge>}<Badge tone={deal ? tone(deal.status) : "slate"}>{deal ? titleCase(deal.stage) : dealLookupAvailable ? "Workspace only" : "Workflow unavailable"}</Badge>{overview.target?.ticker && <Badge tone="neutral">{overview.target.ticker}</Badge>}<span>{overview.target?.sector || titleCase(overview.workspace.deal_type)}</span></span>}
        actions={<div className="flex gap-2"><Button href={`${basePath}/underwriting`}>Open model</Button><Button href={`${basePath}/ic`} variant="secondary">IC workbench</Button></div>}
      />
      <Card eyebrow="Investment question" title={overview.workspace.investment_question || "Investment question has not been defined."} />

      <MetricStrip columns={6}>
        <Metric label="Normalized EBITDA" value={bridgeResult.status === "rejected" ? "Unavailable" : money(bridge?.sponsor_ebitda ?? null, bridge?.currency ?? "USD")} detail={bridge?.period_end ? `LTM ${formatDate(bridge.period_end)}` : "Sponsor QoE bridge"} />
        <Metric label="Entry enterprise value" value={cases === null ? "Unavailable" : money(baseCase?.result.sources_uses.entry_enterprise_value ?? null, baseCase?.result.currency)} detail={baseCase ? `${baseCase.assumptions.transaction.entry_multiple.toFixed(1)}x entry EBITDA` : "Base case required"} />
        <Metric label="Base-case MOIC" value={cases === null ? "Unavailable" : multiple(baseCase?.result.returns.moic ?? null)} tone={(baseCase?.result.returns.moic ?? 0) >= 2 ? "positive" : "warning"} />
        <Metric label="Base-case XIRR" value={cases === null ? "Unavailable" : pct(baseCase?.result.returns.xirr ?? null)} tone={(baseCase?.result.returns.xirr ?? 0) >= 0.2 ? "positive" : "warning"} />
        <Metric label="Open diligence" value={openTasks === null || openRequests === null ? "Unavailable" : openTasks.length + openRequests.length} detail={openTasks && openRequests ? `${openTasks.length} tasks · ${openRequests.length} requests` : "Workflow status"} tone={openTasks && openRequests && openTasks.length + openRequests.length === 0 ? "positive" : "warning"} />
        <Metric label="IC status" value={packets === null ? "Unavailable" : latestPacket ? titleCase(latestPacket.status) : "Not started"} detail={latestPacket ? `Packet v${latestPacket.version}` : "No packet"} tone={latestPacket?.ready_for_submission ? "positive" : "warning"} />
      </MetricStrip>

      <div className="grid gap-6 xl:grid-cols-[1.2fr_.8fr]">
        <Card eyebrow="Underwriting path" title="Deal readiness">
          <div className="grid gap-3 sm:grid-cols-2">
            {readiness.map((item) => <a key={item.label} href={item.href} className="group rounded-md border border-line p-4 transition hover:border-accent/40"><div className="flex items-start justify-between gap-2"><h3 className="font-sans text-sm font-semibold text-ink group-hover:text-accent">{item.label}</h3><Badge tone={item.status === "ready" ? "green" : item.status === "unavailable" ? "red" : "slate"}>{item.status}</Badge></div><p className="mt-1.5 text-xs leading-relaxed text-muted">{item.detail}</p></a>)}
          </div>
        </Card>
        <Card eyebrow="Current process" title="Decision blockers">
          <div className="space-y-3">
            {blockers.map((item) => <a key={item.label} href={item.href} className="flex items-center justify-between border-b border-line-faint pb-2.5 text-xs last:border-0"><span className="text-body">{item.label}</span><span className={`text-base font-semibold tabular-nums ${item.value === null ? "text-muted" : item.value ? "text-negative" : "text-positive"}`}>{item.value === null ? "Unavailable" : item.value}</span></a>)}
          </div>
        </Card>
      </div>

      <Card eyebrow="Scenario comparison" title="Base / upside / downside returns" subtitle="Metrics come from independently saved and hashed case versions.">
        {cases === null ? <Callout tone="warning" title="Model results unavailable">The underwriting service did not respond. No return values are being shown.</Callout> : cases.length ? <DataTable rows={cases} getRowKey={(item) => item.id} columns={[
          { key: "case", header: "Case", render: (item) => <div><Badge tone={item.case_key === "upside" ? "green" : item.case_key === "downside" ? "red" : "indigo"}>{item.case_key}</Badge><div className="mt-1 text-2xs text-faint">v{item.version}</div></div> },
          { key: "entry", header: "Entry multiple", align: "right", render: (item) => multiple(item.assumptions.transaction.entry_multiple) },
          { key: "growth", header: "Revenue CAGR", align: "right", render: (item) => pct(item.result.summary.revenue_cagr) },
          { key: "margin", header: "Exit margin", align: "right", render: (item) => pct(item.result.summary.exit_ebitda_margin) },
          { key: "moic", header: "MOIC", align: "right", render: (item) => multiple(item.result.returns.moic) },
          { key: "irr", header: "XIRR", align: "right", render: (item) => pct(item.result.returns.xirr) },
          { key: "breach", header: "First breach", render: (item) => item.result.summary.first_covenant_breach ? <Badge tone="red">{item.result.summary.first_covenant_breach}</Badge> : <Badge tone="green">None</Badge> },
          { key: "decision", header: "Review", render: (item) => <Badge tone={item.latest_decision ? tone(item.latest_decision.decision) : "slate"}>{item.latest_decision?.decision ?? "Unreviewed"}</Badge> },
        ]} /> : <EmptyPanel title="No operating cases" body="Build the governed case set to populate return metrics." action={<Button href={`${basePath}/underwriting`}>Build case set</Button>} />}
      </Card>

      <Card eyebrow="Risk focus" title="Highest-priority findings" subtitle="Risk absence is never inferred from an unavailable source.">
        <div className="grid gap-4 md:grid-cols-2">
          {overview.top_risks.map((risk) => <article key={risk.id} className="rounded-md border border-line p-4"><div className="flex items-start justify-between gap-3"><h3 className="text-sm font-semibold text-ink">{risk.title}</h3><Badge tone={risk.severity === "critical" || risk.severity === "high" ? "red" : "amber"}>{risk.severity}</Badge></div><p className="mt-2 text-xs leading-relaxed text-muted">{risk.finding}</p><div className="mt-3 flex flex-wrap items-center gap-2 text-2xs text-faint"><span>{risk.risk_category_label}</span>{risk.evidence_ref && <a href={`${basePath}/evidence#${risk.evidence_ref}`} className="font-mono font-semibold text-accent">{risk.evidence_ref} · source excerpt</a>}</div></article>)}
          {ledgerRisks?.slice(0, 4).map((risk) => <article key={risk.id} className="rounded-md border border-line p-4"><div className="flex items-start justify-between gap-3"><h3 className="text-sm font-semibold text-ink">{risk.title}</h3><Badge tone={risk.severity === "critical" || risk.severity === "high" ? "red" : "amber"}>{risk.severity}</Badge></div><p className="mt-2 text-xs leading-relaxed text-muted">{risk.description}</p><div className="mt-3 flex flex-wrap items-center gap-2 text-2xs text-faint"><span>Decision ledger · v{risk.version}</span>{risk.evidence_refs.map((ref) => <a key={ref} href={`${basePath}/evidence#${ref}`} className="font-mono font-semibold text-accent">{ref}</a>)}</div></article>)}
        </div>
        {ledger === null && <div className="mt-4"><Callout tone="warning">The deal ledger is unavailable; workflow risks may be missing from this view.</Callout></div>}
        {!overview.top_risks.length && ledgerRisks && !ledgerRisks.length && <p className="text-xs text-muted">No risk findings have been recorded yet. This is not a conclusion that the deal has no risks.</p>}
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card eyebrow="Source integrity" title="Latest source snapshots">
          {sources === null ? <Callout tone="warning">Source health is unavailable. No clean status is inferred.</Callout> : <DataTable rows={sources.slice(0, 6)} getRowKey={(item) => item.id} empty="No underwriting sources have been imported." columns={[{ key: "name", header: "Source", render: (item) => <div><div className="font-medium text-ink">{item.source_name}</div><div className="text-2xs text-faint">v{item.version} · {item.filename ?? item.source_type}</div></div> }, { key: "records", header: "Records", align: "right", render: (item) => item.record_count.toLocaleString() }, { key: "status", header: "Health", render: (item) => <Badge tone={tone(item.status)}>{item.status}</Badge> }, { key: "sealed", header: "Sealed", render: (item) => formatDate(item.sealed_at) }]} />}
        </Card>
        <Card eyebrow="Priority work" title="Open execution items">
          {openTasks === null ? <Callout tone="warning">Execution tasks are unavailable. No zero-task status is inferred.</Callout> : <DataTable rows={openTasks.slice(0, 8)} getRowKey={(item) => item.id} empty={deal ? "No open tasks are recorded." : "Connect a pipeline deal to manage execution."} columns={[{ key: "task", header: "Task", render: (item) => <div><div className="font-medium text-ink">{item.title}</div><div className="text-2xs text-faint">{item.assignee_actor_id ?? "Unassigned"}</div></div> }, { key: "priority", header: "Priority", render: (item) => <Badge tone={item.priority === "critical" || item.priority === "high" ? "red" : "slate"}>{item.priority}</Badge> }, { key: "due", header: "Due", render: (item) => item.due_date ?? "—" }, { key: "status", header: "Status", render: (item) => <Badge tone={tone(item.status)}>{titleCase(item.status)}</Badge> }]} />}
        </Card>
      </div>

      {!overview.target && <Callout tone="warning" title="Private target required"><div className="flex items-center justify-between gap-3"><span>Create the target and import management financials before starting the QoE bridge or model.</span><Button href={`${basePath}/data-room`} variant="secondary">Set up data room</Button></div></Callout>}
    </div>
  );
}
