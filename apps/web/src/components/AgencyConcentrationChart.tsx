"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART, tickStyle } from "@/lib/chartTheme";
import { formatUsd } from "@/lib/formatting";
import type { AgencyShare } from "@/lib/types";

const MAX_BARS = 8;

type ChartRow = { agency: string; amount: number };

function truncate(label: string, max = 26): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

export function AgencyConcentrationChart({ rows }: { rows: AgencyShare[] }) {
  const data: ChartRow[] = rows
    .map((r) => ({ agency: r.agency ?? "Unknown", amount: r.amount }))
    .sort((a, b) => b.amount - a.amount)
    .slice(0, MAX_BARS);

  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-faint">
        No agency concentration data to chart.
      </p>
    );
  }

  return (
    <div className="w-full" style={{ height: Math.max(160, data.length * 40 + 32) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 8, right: 16, left: 8, bottom: 8 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke={CHART.grid} horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v: number) => formatUsd(v)}
            tick={tickStyle}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
          />
          <YAxis
            type="category"
            dataKey="agency"
            tickFormatter={(v: string) => truncate(v)}
            tick={tickStyle}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            width={150}
          />
          <Tooltip
            formatter={(value: number | string) => [formatUsd(Number(value)), "Obligations"]}
            cursor={{ fill: "rgba(11, 79, 130, 0.06)" }}
            contentStyle={{
              borderRadius: 4,
              border: `1px solid ${CHART.grid}`,
              background: CHART.surface,
              fontSize: 12,
            }}
          />
          <Bar dataKey="amount" fill={CHART.accent} radius={[0, 2, 2, 0]} maxBarSize={22} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default AgencyConcentrationChart;
