import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { StatTile } from "@/components/ui/StatTile";
import { Callout } from "@/components/ui/Callout";
import { formatNumber, formatPct, formatUsd } from "@/lib/formatting";
import type { Target } from "@/lib/types";

export function TargetProfile({ target }: { target: Target }) {
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
        <div className="space-y-4">
          {target.is_synthetic ? (
            <Callout tone="synthetic" title="Synthetic target">
              {target.name} is a synthetic company profile. Every financial figure below is
              illustrative and is not investment advice.
            </Callout>
          ) : (
            <Callout tone="info">
              Financials are real, from SEC EDGAR XBRL company facts
              {target.fiscal_year_end ? ` (FY ${target.fiscal_year_end})` : ""}. Qualitative flags are
              drawn from the latest 10-K. Auto-generated draft — not investment advice.
            </Callout>
          )}

          {target.description && (
            <p className="text-sm leading-relaxed text-slate-700">{target.description}</p>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <StatTile label="Revenue" value={formatUsd(target.revenue)} />
            <StatTile label="Rev. growth" value={formatPct(target.revenue_growth)} />
            <StatTile label="Gross margin" value={formatPct(target.gross_margin)} />
            <StatTile label="Operating margin" value={formatPct(target.operating_margin)} />
            <StatTile label="Net margin" value={formatPct(target.net_margin)} />
            <StatTile label="R&D %" value={formatPct(target.rnd_pct)} />
            <StatTile label="Rule of 40" value={formatPct(target.rule_of_40)} />
            <StatTile label="Cash" value={formatUsd(target.cash)} />
            <StatTile label="Total debt" value={formatUsd(target.total_debt)} />
            <StatTile label="Headcount" value={formatNumber(target.headcount)} />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span>
              Data source: <span className="font-medium text-slate-600">{target.data_source}</span>
            </span>
            <span>AI-assisted draft for human review — not investment advice.</span>
          </div>
        </div>
      </Card>
    </div>
  );
}

export default TargetProfile;
