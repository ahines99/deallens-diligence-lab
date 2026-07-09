"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BenchmarkMetric } from "@/lib/types";

const TARGET_COLOR = "#4338ca"; // brand-600
const PEER_COLOR = "#94a3b8"; // slate-400

type ChartRow = {
  label: string;
  target: number;
  peer: number;
};

function pctTick(value: number): string {
  return `${Math.round(value)}%`;
}

function pctTooltip(value: number | string): string {
  const n = typeof value === "number" ? value : Number(value);
  return `${n.toFixed(1)}%`;
}

export function BenchmarkChart({ metrics }: { metrics: BenchmarkMetric[] }) {
  const data: ChartRow[] = metrics
    .filter(
      (m) =>
        m.unit === "pct" &&
        m.target_value !== null &&
        m.target_value !== undefined &&
        m.peer_median !== null &&
        m.peer_median !== undefined,
    )
    .map((m) => ({
      label: m.label,
      target: (m.target_value as number) * 100,
      peer: (m.peer_median as number) * 100,
    }));

  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        No comparable percentage metrics available to chart.
      </p>
    );
  }

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 12, fill: "#64748b" }}
            tickLine={false}
            axisLine={{ stroke: "#e2e8f0" }}
            interval={0}
          />
          <YAxis
            tickFormatter={pctTick}
            tick={{ fontSize: 12, fill: "#64748b" }}
            tickLine={false}
            axisLine={{ stroke: "#e2e8f0" }}
            width={44}
          />
          <Tooltip
            formatter={(value: number | string) => pctTooltip(value)}
            cursor={{ fill: "rgba(148, 163, 184, 0.12)" }}
            contentStyle={{
              borderRadius: 8,
              border: "1px solid #e2e8f0",
              fontSize: 12,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar name="Target" dataKey="target" fill={TARGET_COLOR} radius={[4, 4, 0, 0]} maxBarSize={48} />
          <Bar
            name="Peer median"
            dataKey="peer"
            fill={PEER_COLOR}
            radius={[4, 4, 0, 0]}
            maxBarSize={48}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default BenchmarkChart;
