import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { AddPeersForm } from "@/components/AddPeersForm";
import { AutoCompsButton } from "@/components/AutoCompsButton";
import { CompsTable } from "@/components/CompsTable";
import { BenchmarkChart } from "@/components/BenchmarkChart";
import { formatMultiple, formatPct, formatUsd } from "@/lib/formatting";
import type { BenchmarkMetric, ComparableCompany, FinancialBenchmark } from "@/lib/types";

const ASSESSMENT_TONE: Record<BenchmarkMetric["assessment"], BadgeTone> = {
  above: "green",
  in_line: "slate",
  below: "red",
  "n/a": "neutral",
};

const ASSESSMENT_LABEL: Record<BenchmarkMetric["assessment"], string> = {
  above: "Above",
  in_line: "In line",
  below: "Below",
  "n/a": "N/A",
};

function formatByUnit(unit: BenchmarkMetric["unit"], v: number | null): string {
  if (v === null || v === undefined) return "—";
  switch (unit) {
    case "pct":
      return formatPct(v, 0);
    case "x":
      return formatMultiple(v);
    case "usd":
      return formatUsd(v);
    case "ratio":
      return v.toFixed(2);
    default:
      return String(v);
  }
}

export default async function CompsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let comps: ComparableCompany[] | null = null;
  let error: string | null = null;
  try {
    comps = await api.getComps(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load comparable companies.";
  }

  const benchmark: FinancialBenchmark | null =
    error || !comps || comps.length === 0 ? null : await api.getBenchmark(id).catch(() => null);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Comps & benchmark"
        subtitle="Add real public-company peers by ticker to benchmark the target's financials against the peer set."
      />

      <Card title="Add peer companies" subtitle="Pulled live from SEC XBRL company facts">
        <div className="space-y-4">
          <AddPeersForm workspaceId={id} />
          <div className="border-t border-line-faint pt-4">
            <AutoCompsButton workspaceId={id} />
          </div>
        </div>
      </Card>

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : comps && comps.length > 0 ? (
        <>
          <Card title="Comparable companies" subtitle={`${comps.length} peers`}>
            <CompsTable comps={comps} />
          </Card>

          {benchmark && (
            <Card
              title="Financial benchmark"
              subtitle={`${benchmark.target_name} vs. ${benchmark.peer_count} peers`}
            >
              <div className="space-y-6">
                {benchmark.summary && (
                  <p className="max-w-measure text-sm leading-relaxed text-body">{benchmark.summary}</p>
                )}

                <div>
                  <h4 className="eyebrow mb-3">Target vs. peer median (margins &amp; growth)</h4>
                  <BenchmarkChart metrics={benchmark.metrics} />
                </div>

                <div>
                  <h4 className="eyebrow mb-3">Metric detail</h4>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Metric</TH>
                        <TH align="right">Target</TH>
                        <TH align="right">Peer median</TH>
                        <TH align="right">Peer min</TH>
                        <TH align="right">Peer max</TH>
                        <TH>Assessment</TH>
                        <TH>Commentary</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {benchmark.metrics.map((m) => (
                        <TR key={m.key} className="hover:bg-panel2">
                          <TD className="font-medium text-ink">{m.label}</TD>
                          <TD align="right" className="tabular-nums">
                            {formatByUnit(m.unit, m.target_value)}
                          </TD>
                          <TD align="right" className="tabular-nums">
                            {formatByUnit(m.unit, m.peer_median)}
                          </TD>
                          <TD align="right" className="tabular-nums">
                            {formatByUnit(m.unit, m.peer_min)}
                          </TD>
                          <TD align="right" className="tabular-nums">
                            {formatByUnit(m.unit, m.peer_max)}
                          </TD>
                          <TD>
                            <Badge tone={ASSESSMENT_TONE[m.assessment] ?? "neutral"}>
                              {ASSESSMENT_LABEL[m.assessment] ?? m.assessment}
                            </Badge>
                          </TD>
                          <TD className="max-w-xs text-xs text-muted">{m.commentary}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </div>

                {benchmark.notes.length > 0 && (
                  <Callout tone="info" title="Notes">
                    <ul className="list-disc space-y-1 pl-4">
                      {benchmark.notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  </Callout>
                )}
              </div>
            </Card>
          )}
        </>
      ) : (
        <EmptyState
          title="No comparable companies yet"
          description="Add real public-company peer tickers above (e.g. NVDA, CRM, ORCL) to benchmark the target's margins, growth, and R&D intensity against the peer set. Market multiples are intentionally omitted — no free market-data source."
        />
      )}
    </div>
  );
}
