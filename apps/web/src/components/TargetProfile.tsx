import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { StatTile } from "@/components/ui/StatTile";
import { Callout } from "@/components/ui/Callout";
import { formatNumber, formatPct, formatUsd } from "@/lib/formatting";
import type { Target } from "@/lib/types";

export function TargetProfile({ target }: { target: Target }) {
  const kpis = [
    { label: "Revenue", value: formatUsd(target.revenue) },
    { label: "Rev. growth", value: formatPct(target.revenue_growth) },
    { label: "Gross margin", value: formatPct(target.gross_margin) },
    { label: "Operating margin", value: formatPct(target.operating_margin) },
    { label: "Net margin", value: formatPct(target.net_margin) },
    { label: "R&D %", value: formatPct(target.rnd_pct) },
    { label: "Rule of 40", value: formatPct(target.rule_of_40) },
    { label: "Cash", value: formatUsd(target.cash) },
    { label: "Total debt", value: formatUsd(target.total_debt) },
    { label: "Headcount", value: formatNumber(target.headcount) },
  ];

  return (
    <div className="space-y-6">
      <Card
        title={target.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {target.ticker && <Badge tone="indigo">{target.ticker}</Badge>}
            {target.cik && <Badge tone="neutral">CIK {target.cik}</Badge>}
            <Badge tone="slate">{target.sector}</Badge>
            {target.fiscal_year_end && (
              <Badge tone="neutral">FY {target.fiscal_year_end}</Badge>
            )}
          </span>
        }
      >
        <div className="space-y-5">
          {target.is_synthetic ? (
            <Callout tone="synthetic" title="Synthetic target">
              {target.name} is a synthetic company profile. Every financial figure below is
              illustrative and is not investment advice.
            </Callout>
          ) : (
            <Callout tone="info" title="SEC XBRL company facts">
              Financials are real, from SEC EDGAR XBRL company facts
              {target.fiscal_year_end ? ` (FY ${target.fiscal_year_end})` : ""}. Qualitative flags are
              drawn from the latest 10-K. Auto-generated draft — not investment advice.
            </Callout>
          )}

          {target.description && (
            <p className="max-w-measure text-sm leading-relaxed text-body">{target.description}</p>
          )}

          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line md:grid-cols-5">
            {kpis.map((k) => (
              <div key={k.label} className="bg-panel p-4">
                <StatTile label={k.label} value={k.value} />
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-faint pt-3 text-xs text-faint">
            <span>
              Data source: <span className="font-medium text-muted">{target.data_source}</span>
            </span>
            <span>AI-assisted draft for human review — not investment advice.</span>
          </div>
        </div>
      </Card>
    </div>
  );
}

export default TargetProfile;
