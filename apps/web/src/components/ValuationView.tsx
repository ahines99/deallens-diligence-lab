import { Card } from "@/components/ui/Card";
import { Callout } from "@/components/ui/Callout";
import { StatTile } from "@/components/ui/StatTile";
import { LboCalculator } from "@/components/LboCalculator";
import { formatUsd, formatPct } from "@/lib/formatting";
import type { Valuation } from "@/lib/types";

function pct(v: number | null, digits = 1): string {
  return v === null || v === undefined ? "n/a" : formatPct(v, digits);
}

export function ValuationView({ data, workspaceId }: { data: Valuation; workspaceId: string }) {
  const { wacc, dcf } = data;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line shadow-panel sm:grid-cols-4">
        {[
          { label: "EBITDA", value: data.ebitda === null ? "n/a" : formatUsd(data.ebitda) },
          { label: "Net debt", value: data.net_debt === null ? "n/a" : formatUsd(data.net_debt) },
          { label: "WACC", value: pct(wacc.value), tone: "accent" as const },
          { label: "DCF enterprise value", value: dcf.enterprise_value === null ? "n/a" : formatUsd(dcf.enterprise_value), tone: "accent" as const },
        ].map((t) => (
          <div key={t.label} className="bg-panel px-4 py-4">
            <StatTile label={t.label} value={t.value} tone={t.tone ?? "default"} />
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Cost of capital (WACC)" subtitle="CAPM cost of equity + after-tax cost of debt, weighted">
          <dl className="divide-y divide-line-faint text-sm">
            {[
              ["WACC", pct(wacc.value)],
              ["Risk-free rate (FRED DGS10)", pct(wacc.risk_free, 2)],
              ["Equity risk premium", pct(wacc.equity_risk_premium)],
              ["Beta", wacc.beta.toFixed(2)],
              ["Cost of equity", pct(wacc.cost_of_equity)],
              ["Cost of debt (pre-tax)", pct(wacc.cost_of_debt)],
              ["Tax rate", pct(wacc.tax_rate, 0)],
              ["Debt weight", pct(wacc.debt_weight, 0)],
            ].map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-4 py-1.5">
                <dt className="text-muted">{k}</dt>
                <dd className="tabular-nums font-medium text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card title="DCF-lite" subtitle="5-year FCF projection + Gordon terminal value, discounted at WACC">
          <dl className="divide-y divide-line-faint text-sm">
            {[
              ["Base FCF", dcf.fcf_base === null ? "n/a" : formatUsd(dcf.fcf_base)],
              ["Growth (yrs 1–5)", pct(dcf.growth)],
              ["Terminal growth", pct(dcf.terminal_growth)],
              ["Discount rate (WACC)", pct(dcf.wacc)],
              ["Enterprise value", dcf.enterprise_value === null ? "n/a" : formatUsd(dcf.enterprise_value)],
            ].map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-4 py-1.5">
                <dt className="text-muted">{k}</dt>
                <dd className="tabular-nums font-medium text-ink">{v}</dd>
              </div>
            ))}
          </dl>
          {dcf.assumptions.length > 0 && (
            <Callout tone="muted" title="Assumptions" className="mt-4">
              <ul className="list-disc space-y-1 pl-4">
                {dcf.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </Callout>
          )}
        </Card>
      </div>

      <Card
        title="LBO returns model"
        subtitle="Interactive — adjust entry/exit multiples, leverage, hold and growth to size returns"
      >
        <LboCalculator
          workspaceId={workspaceId}
          initial={{
            entry_multiple: 10,
            exit_multiple: 10,
            leverage: 5,
            hold_years: 5,
            ebitda_cagr: 0.08,
          }}
        />
      </Card>

      {data.notes.length > 0 && (
        <Callout tone="info" title="Notes">
          <ul className="list-disc space-y-1 pl-4">
            {data.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </Callout>
      )}
    </div>
  );
}

export default ValuationView;
