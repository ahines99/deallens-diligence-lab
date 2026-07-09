"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { CHART } from "@/lib/chartTheme";
import { formatDate, formatNumber, formatPct } from "@/lib/formatting";
import type { MacroOverlay, MacroSeries } from "@/lib/types";

function formatLatest(series: MacroSeries): string {
  const rounded = Math.round(series.latest_value * 100) / 100;
  const num = formatNumber(rounded);
  return series.unit === "pct" ? `${num}%` : num;
}

// yoy_change is a decimal (e.g. 0.023 = +2.3%). Prepend a "+" for non-negative;
// formatPct already carries the minus sign for negatives.
function formatYoy(yoy: number): string {
  return `${yoy >= 0 ? "+" : ""}${formatPct(yoy, 1)}`;
}

function Sparkline({ series }: { series: MacroSeries }) {
  const points = series.points.slice(-36);
  if (points.length < 2) {
    return <div className="h-12 w-full" aria-hidden />;
  }
  return (
    <div className="h-12 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Tooltip
            formatter={(value: number | string) => [
              series.unit === "pct"
                ? `${Number(value).toFixed(2)}%`
                : formatNumber(Number(value)),
              series.label,
            ]}
            labelFormatter={(label) => formatDate(String(label))}
            contentStyle={{
              borderRadius: 4,
              border: `1px solid ${CHART.grid}`,
              background: CHART.surface,
              fontSize: 12,
            }}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke={CHART.accent}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MacroPanel({ macro }: { macro: MacroOverlay }) {
  return (
    <div className="space-y-5">
      {macro.commentary && (
        <Callout tone="info" title="Macro context">
          {macro.commentary}
        </Callout>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {macro.series.map((s) => (
          <div
            key={s.series_id}
            className="flex flex-col rounded-md border border-line bg-panel p-4 shadow-panel"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-ink">{s.label}</div>
                {s.note && <p className="mt-0.5 text-xs leading-snug text-muted">{s.note}</p>}
              </div>
              {s.yoy_change !== null && s.yoy_change !== undefined && (
                <Badge tone={s.yoy_change <= 0 ? "green" : "amber"}>{formatYoy(s.yoy_change)} YoY</Badge>
              )}
            </div>

            <div className="mt-3 flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums text-ink">
                {formatLatest(s)}
              </span>
              <span className="text-xs text-faint">as of {formatDate(s.latest_date)}</span>
            </div>

            <div className="mt-3">
              <Sparkline series={s} />
            </div>
          </div>
        ))}
      </div>

      <p className="text-2xs text-faint">Source: FRED (St. Louis Fed).</p>
    </div>
  );
}

export default MacroPanel;
