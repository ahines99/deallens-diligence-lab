"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART, SERIES_COLOR, tickStyle } from "@/lib/chartTheme";
import { formatUsd } from "@/lib/formatting";
import type { TrendPoint } from "@/lib/types";

// Minimal, hairline-bordered tooltip shared by both panels.
const TOOLTIP_STYLE = {
  borderRadius: 4,
  border: `1px solid ${CHART.grid}`,
  background: CHART.surface,
  fontSize: 12,
} as const;

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

function ChartLabel({ children }: { children: ReactNode }) {
  return (
    <p className="mb-2 text-2xs font-semibold uppercase tracking-eyebrow text-muted">{children}</p>
  );
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
      <p className="py-8 text-center text-sm text-faint">
        No multi-year data points available to chart.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {/* Revenue — single-axis bar chart (USD) */}
      <div>
        <ChartLabel>Revenue</ChartLabel>
        <div className="h-56 w-full" role="img" aria-label="Revenue by fiscal year, bar chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART.grid} vertical={false} />
              <XAxis
                dataKey="year"
                tick={tickStyle}
                tickLine={false}
                axisLine={{ stroke: CHART.axis }}
              />
              <YAxis
                tickFormatter={(v: number) => formatUsd(v)}
                tick={tickStyle}
                tickLine={false}
                axisLine={{ stroke: CHART.axis }}
                width={56}
              />
              <Tooltip
                formatter={(value: number | string) => [formatUsd(Number(value)), "Revenue"]}
                cursor={{ fill: "rgba(11, 79, 130, 0.06)" }}
                contentStyle={TOOLTIP_STYLE}
              />
              <Bar
                name="Revenue"
                dataKey="revenue"
                fill={CHART.accent}
                radius={[2, 2, 0, 0]}
                maxBarSize={48}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Margins — single-axis line chart (percent) */}
      <div>
        <ChartLabel>Margins</ChartLabel>
        <div
          className="h-56 w-full"
          role="img"
          aria-label="Gross, operating, and net margin by fiscal year, line chart"
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART.grid} vertical={false} />
              <XAxis
                dataKey="year"
                tick={tickStyle}
                tickLine={false}
                axisLine={{ stroke: CHART.axis }}
              />
              <YAxis
                tickFormatter={pctTick}
                tick={tickStyle}
                tickLine={false}
                axisLine={{ stroke: CHART.axis }}
                width={44}
              />
              <Tooltip
                formatter={(value: number | string, name: string) => [
                  `${Number(value).toFixed(1)}%`,
                  name,
                ]}
                cursor={{ stroke: CHART.axis, strokeWidth: 1 }}
                contentStyle={TOOLTIP_STYLE}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                name="Gross margin"
                type="monotone"
                dataKey="gross"
                stroke={SERIES_COLOR.gross_margin}
                strokeWidth={2}
                dot={{ r: 2.5 }}
                connectNulls={false}
              />
              <Line
                name="Operating margin"
                type="monotone"
                dataKey="operating"
                stroke={SERIES_COLOR.operating_margin}
                strokeWidth={2}
                dot={{ r: 2.5 }}
                connectNulls={false}
              />
              <Line
                name="Net margin"
                type="monotone"
                dataKey="net"
                stroke={SERIES_COLOR.net_margin}
                strokeWidth={2}
                dot={{ r: 2.5 }}
                connectNulls={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default TrendChart;
