import Link from "next/link";
import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { StatTile } from "@/components/ui/StatTile";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { GenerateButton, type GenerateKind } from "@/components/GenerateButton";
import { SourceCitation } from "@/components/SourceCitation";
import { DEAL_TYPE_LABELS, formatPct, formatUsd, titleCase } from "@/lib/formatting";
import type { Severity, WorkspaceOverview } from "@/lib/types";

const SEVERITY_TONE: Record<Severity, BadgeTone> = {
  low: "green",
  medium: "amber",
  high: "red",
  critical: "critical",
};
const PLAN_STATUS_TONE: Record<string, BadgeTone> = {
  planned: "slate",
  in_progress: "amber",
  complete: "green",
};

export default async function WorkspaceOverviewPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;
  const base = `/workspaces/${id}`;

  let overview: WorkspaceOverview;
  try {
    overview = await api.getWorkspace(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return (
      <Callout tone="warning" title="Can't reach the API">
        {e instanceof ApiError ? e.message : "Failed to load this workspace."} Start the backend
        service (<code className="font-mono">apps/api</code>) and refresh.
      </Callout>
    );
  }

  const plan = await api.getPlan(id).catch(() => null);
  const { workspace, target, counts, artifacts, top_risks } = overview;

  const kpis = target
    ? [
        { label: "Revenue", value: formatUsd(target.revenue) },
        { label: "Rev. growth", value: formatPct(target.revenue_growth) },
        { label: "Gross margin", value: formatPct(target.gross_margin) },
        { label: "Op. margin", value: formatPct(target.operating_margin) },
        { label: "Net margin", value: formatPct(target.net_margin) },
        { label: "Rule of 40", value: formatPct(target.rule_of_40) },
      ]
    : [];

  const explorers = [
    { label: "Trends", href: `${base}/trends`, desc: "Multi-year revenue & margins (XBRL)." },
    { label: "Macro", href: `${base}/macro`, desc: "FRED indicators for the sector." },
    { label: "GovCon", href: `${base}/govcon`, desc: "Federal award exposure (USAspending)." },
  ];

  const artifactRows: { key: string; label: string; done: boolean; kind: GenerateKind; href: string }[] = [
    { key: "plan", label: "Diligence plan", done: artifacts.plan, kind: "plan", href: base },
    { key: "risks", label: "Red-flag matrix", done: artifacts.risks, kind: "risks", href: `${base}/risks` },
    { key: "questions", label: "Diligence questions", done: artifacts.questions, kind: "questions", href: `${base}/questions` },
    { key: "ic_memo", label: "IC memo", done: artifacts.ic_memo, kind: "memo", href: `${base}/memo` },
    { key: "bear_case", label: "Red-team bear case", done: artifacts.bear_case, kind: "red-team", href: `${base}/red-team` },
  ];
  const doneCount = artifactRows.filter((a) => a.done).length;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Diligence overview"
        title={target?.name ?? workspace.name}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            <Badge tone="indigo">{DEAL_TYPE_LABELS[workspace.deal_type] ?? workspace.deal_type}</Badge>
            {target?.fiscal_year_end && <Badge tone="neutral">FY {target.fiscal_year_end}</Badge>}
            {target?.data_source && <span className="text-2xs text-faint">{target.data_source}</span>}
          </span>
        }
        actions={
          artifacts.ic_memo ? (
            <Button href={`${base}/memo`}>View IC memo</Button>
          ) : (
            <GenerateButton kind="memo" workspaceId={id} label="Generate IC memo" />
          )
        }
      />

      {/* Investment question */}
      <Card eyebrow="Investment question" title={workspace.investment_question} />

      {/* Target KPI strip */}
      {target && (
        <Card
          eyebrow="Financial profile"
          title="Reported fundamentals"
          subtitle="From SEC XBRL company facts — not investment advice."
          right={
            <Button href={`${base}/target`} variant="secondary">
              Full profile
            </Button>
          }
          bodyClassName=""
        >
          <div className="grid grid-cols-2 gap-px border-t border-line bg-line sm:grid-cols-3 lg:grid-cols-6">
            {kpis.map((k) => (
              <div key={k.label} className="bg-panel px-5 py-4">
                <StatTile label={k.label} value={k.value} />
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Explorers */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {explorers.map((q) => (
          <Link
            key={q.label}
            href={q.href}
            className="group flex items-start justify-between gap-3 rounded-md border border-line bg-panel p-4 shadow-panel transition hover:border-accent/40"
          >
            <div>
              <div className="text-sm font-semibold text-ink">{q.label}</div>
              <p className="mt-1 text-xs leading-snug text-muted">{q.desc}</p>
            </div>
            <span className="text-faint transition group-hover:text-accent" aria-hidden>
              →
            </span>
          </Link>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Artifacts / progress */}
        <Card eyebrow="Deliverables" title="Artifacts & progress" subtitle={`${doneCount} of ${artifactRows.length} generated`}>
          <ul className="divide-y divide-line-faint">
            {artifactRows.map((a) => (
              <li key={a.key} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="flex items-center gap-2.5">
                  <span
                    className={`h-4 w-0.5 rounded-full ${a.done ? "bg-accent" : "bg-line-strong"}`}
                    aria-hidden
                  />
                  <span className="text-sm text-body">{a.label}</span>
                </div>
                {a.done ? (
                  <div className="flex items-center gap-2">
                    <Badge tone="green">Ready</Badge>
                    <Button href={a.href} variant="ghost">
                      View
                    </Button>
                  </div>
                ) : (
                  <GenerateButton kind={a.kind} workspaceId={id} variant="secondary" label="Generate" />
                )}
              </li>
            ))}
          </ul>
        </Card>

        {/* Top risks */}
        <Card
          eyebrow="Red flags"
          title="Highest-severity findings"
          right={
            artifacts.risks ? (
              <Button href={`${base}/risks`} variant="ghost">
                Full matrix →
              </Button>
            ) : undefined
          }
        >
          {top_risks.length > 0 ? (
            <ul className="space-y-2.5">
              {top_risks.map((r) => (
                <li key={r.id} className="border-l-2 border-line pl-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-medium text-ink">{r.title}</p>
                    <Badge tone={SEVERITY_TONE[r.severity]}>
                      {titleCase(r.severity)} · {r.severity_score}/10
                    </Badge>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">{r.finding}</p>
                  <div className="mt-1.5 flex items-center gap-2 text-2xs text-faint">
                    <span className="uppercase tracking-wide">{r.risk_category_label}</span>
                    {r.evidence_ref && <SourceCitation evidenceRef={r.evidence_ref} workspaceId={id} />}
                  </div>
                </li>
              ))}
            </ul>
          ) : artifacts.risks ? (
            <p className="text-sm text-muted">No material red flags were surfaced.</p>
          ) : (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-muted">
                Run the risk screen to surface the highest-severity red flags, each tied to evidence.
              </p>
              <GenerateButton kind="risks" workspaceId={id} label="Screen for red flags" />
            </div>
          )}
        </Card>
      </div>

      {/* Diligence plan */}
      <Card
        eyebrow="Plan"
        title="Diligence plan"
        subtitle={plan ? "Workstreams and objectives" : undefined}
        right={plan ? <GenerateButton kind="plan" workspaceId={id} label="Regenerate" variant="secondary" /> : undefined}
      >
        {plan ? (
          <div className="space-y-4">
            <p className="max-w-measure text-sm leading-relaxed text-body">{plan.summary}</p>
            <ul className="grid gap-3 sm:grid-cols-2">
              {plan.workstreams.map((ws) => (
                <li key={ws.workstream} className="rounded-md border border-line p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-semibold text-ink">{ws.workstream_label}</p>
                    <Badge tone={PLAN_STATUS_TONE[ws.status] ?? "slate"}>{titleCase(ws.status)}</Badge>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{ws.objective}</p>
                  {ws.key_questions.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted">
                      {ws.key_questions.slice(0, 2).map((q, i) => (
                        <li key={i}>{q}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-muted">
              No plan yet. Generate a workstream diligence plan aligned to this investment question.
            </p>
            <GenerateButton kind="plan" workspaceId={id} label="Generate diligence plan" />
          </div>
        )}
      </Card>
    </div>
  );
}
