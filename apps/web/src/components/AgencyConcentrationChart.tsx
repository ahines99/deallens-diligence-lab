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
import { formatUsd } from "@/lib/formatting";
import type { AgencyShare } from "@/lib/types";

const BAR_COLOR = "#4338ca"; // brand-600
const GRID = "#e2e8f0"; // slate-200
const AXIS_TEXT = "#64748b"; // slate-500

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
      <p className="py-8 text-center text-sm text-slate-400">
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
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v: number) => formatUsd(v)}
            tick={{ fontSize: 12, fill: AXIS_TEXT }}
            tickLine={false}
            axisLine={{ stroke: GRID }}
          />
          <YAxis
            type="category"
            dataKey="agency"
            tickFormatter={(v: string) => truncate(v)}
            tick={{ fontSize: 11, fill: AXIS_TEXT }}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            width={150}
          />
          <Tooltip
            formatter={(value: number | string) => [formatUsd(Number(value)), "Obligations"]}
            cursor={{ fill: "rgba(148, 163, 184, 0.12)" }}
            contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12 }}
          />
          <Bar dataKey="amount" fill={BAR_COLOR} radius={[0, 4, 4, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default AgencyConcentrationChart;
