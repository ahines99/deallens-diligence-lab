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
import type { Severity, WorkspaceOverview, WorkspaceStatus } from "@/lib/types";

const SEVERITY_TONE: Record<Severity, BadgeTone> = {
  low: "green",
  medium: "amber",
  high: "red",
  critical: "red",
};

const STATUS_TONE: Record<WorkspaceStatus, BadgeTone> = {
  draft: "slate",
  in_progress: "amber",
  complete: "green",
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

  const countTiles = [
    { label: "Filings", value: counts.filings, href: `${base}/filings` },
    { label: "Comps", value: counts.comps, href: `${base}/comps` },
    { label: "Risks", value: counts.risks, href: `${base}/risks` },
    { label: "Questions", value: counts.questions, href: `${base}/questions` },
    { label: "Evidence", value: counts.evidence, href: `${base}/evidence` },
  ];

  const artifactRows: {
    key: string;
    label: string;
    done: boolean;
    kind: GenerateKind;
    href: string;
  }[] = [
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
        title={workspace.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="indigo">{DEAL_TYPE_LABELS[workspace.deal_type] ?? workspace.deal_type}</Badge>
            <Badge tone={STATUS_TONE[workspace.status]}>{titleCase(workspace.status)}</Badge>
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
      <Card title="Investment question">
        <p className="text-sm leading-relaxed text-slate-700">{workspace.investment_question}</p>
      </Card>

      {/* Target summary strip */}
      {target ? (
        <Card
          title="Target"
          subtitle={
            <span className="flex flex-wrap items-center gap-2">
              {target.ticker && <Badge tone="indigo">{target.ticker}</Badge>}
              <span>{target.name}</span>
              <span className="text-slate-400">·</span>
              <span>{target.sector}</span>
              {target.fiscal_year_end && (
                <Badge tone="neutral">FY {target.fiscal_year_end}</Badge>
              )}
            </span>
          }
          right={<Button href={`${base}/target`} variant="secondary">Target profile</Button>}
        >
          <div className="space-y-4">
            {target.is_synthetic ? (
              <Callout tone="synthetic" title="Synthetic target">
                {target.name} is a synthetic company profile. All financials below are illustrative and
                are not investment advice.
              </Callout>
            ) : (
              <Callout tone="info">
                Financials are real, from SEC EDGAR XBRL company facts
                {target.fiscal_year_end ? ` (FY ${target.fiscal_year_end})` : ""}. Qualitative flags
                are drawn from the latest 10-K. Auto-generated draft — not investment advice.
              </Callout>
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <StatTile label="Revenue" value={formatUsd(target.revenue)} />
              <StatTile label="Rev. growth" value={formatPct(target.revenue_growth)} />
              <StatTile label="Gross margin" value={formatPct(target.gross_margin)} />
              <StatTile label="Operating margin" value={formatPct(target.operating_margin)} />
              <StatTile label="Net margin" value={formatPct(target.net_margin)} />
              <StatTile label="Rule of 40" value={formatPct(target.rule_of_40)} />
            </div>
          </div>
        </Card>
      ) : (
        <Card title="Target">
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-slate-500">
              No target attached yet. Add a company profile to anchor benchmarks and risk screening.
            </p>
            <Button href={`${base}/target`} variant="secondary">
              Add target
            </Button>
          </div>
        </Card>
      )}

      {/* Counts dashboard */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {countTiles.map((c) => (
          <Link key={c.label} href={c.href} className="block transition hover:opacity-80">
            <StatTile label={c.label} value={c.value} />
          </Link>
        ))}
      </div>

      {/* Real-data explorers */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[
          {
            label: "Trends",
            href: `${base}/trends`,
            desc: "Multi-year revenue & margin history from SEC XBRL.",
          },
          {
            label: "Macro",
            href: `${base}/macro`,
            desc: "FRED macro indicators relevant to the target's sector.",
          },
          {
            label: "GovCon",
            href: `${base}/govcon`,
            desc: "Federal contract exposure & recompete risk (USAspending.gov).",
          },
        ].map((q) => (
          <Link
            key={q.label}
            href={q.href}
            className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-brand-300 hover:shadow"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-slate-900">{q.label}</span>
              <span className="text-slate-400" aria-hidden>
                →
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">{q.desc}</p>
          </Link>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Artifacts / progress checklist */}
        <Card
          title="Artifacts & progress"
          subtitle={`${doneCount} of ${artifactRows.length} generated`}
        >
          <ul className="divide-y divide-slate-100">
            {artifactRows.map((a) => (
              <li key={a.key} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] ${
                      a.done ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-400"
                    }`}
                    aria-hidden
                  >
                    {a.done ? "✓" : "•"}
                  </span>
                  <span className="text-sm font-medium text-slate-800">{a.label}</span>
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
          title="Top risks"
          subtitle="Highest-severity red flags"
          right={
            artifacts.risks ? (
              <Button href={`${base}/risks`} variant="secondary">
                Full matrix
              </Button>
            ) : undefined
          }
        >
          {top_risks.length > 0 ? (
            <ul className="space-y-3">
              {top_risks.map((r) => (
                <li key={r.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-medium text-slate-800">{r.title}</p>
                    <Badge tone={SEVERITY_TONE[r.severity]}>
                      {titleCase(r.severity)} · {r.severity_score}/10
                    </Badge>
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-slate-600">{r.finding}</p>
                  <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                    <span>{r.risk_category_label}</span>
                    {r.evidence_ref && (
                      <SourceCitation evidenceRef={r.evidence_ref} workspaceId={id} />
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : artifacts.risks ? (
            <p className="text-sm text-slate-500">No material red flags were surfaced.</p>
          ) : (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-slate-500">
                Run the risk screen to surface the target&apos;s highest-severity red flags, each tied to
                evidence.
              </p>
              <GenerateButton kind="risks" workspaceId={id} label="Screen for red flags" />
            </div>
          )}
        </Card>
      </div>

      {/* Diligence plan */}
      <Card
        title="Diligence plan"
        subtitle={plan ? "Workstreams and objectives" : undefined}
        right={
          plan ? (
            <GenerateButton kind="plan" workspaceId={id} label="Regenerate" variant="secondary" />
          ) : undefined
        }
      >
        {plan ? (
          <div className="space-y-4">
            <p className="text-sm leading-relaxed text-slate-700">{plan.summary}</p>
            <ul className="space-y-3">
              {plan.workstreams.map((ws) => (
                <li key={ws.workstream} className="rounded-lg border border-slate-200 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-semibold text-slate-800">{ws.workstream_label}</p>
                    <Badge tone={PLAN_STATUS_TONE[ws.status] ?? "slate"}>{titleCase(ws.status)}</Badge>
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{ws.objective}</p>
                  {ws.key_questions.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">
                      {ws.key_questions.slice(0, 3).map((q, i) => (
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
            <p className="text-sm text-slate-500">
              No plan yet. Generate a workstream diligence plan aligned to this investment question.
            </p>
            <GenerateButton kind="plan" workspaceId={id} label="Generate diligence plan" />
          </div>
        )}
      </Card>
    </div>
  );
}
