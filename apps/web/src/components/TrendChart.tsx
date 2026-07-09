"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatUsd } from "@/lib/formatting";
import type { TrendPoint } from "@/lib/types";

// House brand for the revenue magnitude; a CVD-safe trio for the three margin
// lines (validated: worst adjacent ΔE 92.6, well clear of the ≥12 target).
const REVENUE_COLOR = "#4338ca"; // brand-600
const GROSS_COLOR = "#1baf7a"; // aqua
const OPERATING_COLOR = "#eb6834"; // orange
const NET_COLOR = "#2a78d6"; // blue

const GRID = "#e2e8f0"; // slate-200
const AXIS_TEXT = "#64748b"; // slate-500

type ChartRow = {
  year: string;
  revenue: number | null;
  gross: number | null;
  operating: number | null;
  net: number | null;
};

// Margins are decimals in [0,1] on the wire; render on a percent axis.
function toPct(value: number | null): number | null {
  return value === null || value === undefined ? null : value * 100;
}

function pctTick(value: number): string {
  return `${Math.round(value)}%`;
}

function tooltipFormatter(value: number | string, name: string): [string, string] {
  const n = typeof value === "number" ? value : Number(value);
  if (name === "Revenue") return [formatUsd(n), name];
  return [`${n.toFixed(1)}%`, name];
}

export function TrendChart({ rows }: { rows: TrendPoint[] }) {
  const data: ChartRow[] = rows.map((r) => ({
    year: r.year,
    revenue: r.revenue ?? null,
    gross: toPct(r.gross_margin),
    operating: toPct(r.operating_margin),
    net: toPct(r.net_margin),
  }));

  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        No multi-year data points available to chart.
      </p>
    );
  }

  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
          <XAxis
            dataKey="year"
            tick={{ fontSize: 12, fill: AXIS_TEXT }}
            tickLine={false}
            axisLine={{ stroke: GRID }}
          />
          <YAxis
            yAxisId="rev"
            tickFormatter={(v: number) => formatUsd(v)}
            tick={{ fontSize: 12, fill: AXIS_TEXT }}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            width={56}
          />
          <YAxis
            yAxisId="pct"
            orientation="right"
            tickFormatter={pctTick}
            tick={{ fontSize: 12, fill: AXIS_TEXT }}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            width={44}
          />
          <Tooltip
            formatter={tooltipFormatter}
            cursor={{ fill: "rgba(148, 163, 184, 0.12)" }}
            contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar
            yAxisId="rev"
            name="Revenue"
            dataKey="revenue"
            fill={REVENUE_COLOR}
            radius={[4, 4, 0, 0]}
            maxBarSize={56}
          />
          <Line
            yAxisId="pct"
            name="Gross margin"
            type="monotone"
            dataKey="gross"
            stroke={GROSS_COLOR}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls={false}
          />
          <Line
            yAxisId="pct"
            name="Operating margin"
            type="monotone"
            dataKey="operating"
            stroke={OPERATING_COLOR}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls={false}
          />
          <Line
            yAxisId="pct"
            name="Net margin"
            type="monotone"
            dataKey="net"
            stroke={NET_COLOR}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export default TrendChart;
