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
import { CHART, SERIES_COLOR, tickStyle } from "@/lib/chartTheme";
import type { BenchmarkMetric } from "@/lib/types";

const TARGET_COLOR = CHART.accent;
const PEER_COLOR = SERIES_COLOR.peer;

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
      <p className="py-8 text-center text-sm text-faint">
        No comparable percentage metrics available to chart.
      </p>
    );
  }

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={tickStyle}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            interval={0}
          />
          <YAxis
            tickFormatter={pctTick}
            tick={tickStyle}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            width={44}
          />
          <Tooltip
            formatter={(value: number | string) => pctTooltip(value)}
            cursor={{ fill: "rgba(11, 79, 130, 0.06)" }}
            contentStyle={{
              borderRadius: 4,
              border: `1px solid ${CHART.grid}`,
              backgroundColor: CHART.surface,
              fontSize: 11,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar name="Target" dataKey="target" fill={TARGET_COLOR} radius={[2, 2, 0, 0]} maxBarSize={44} />
          <Bar
            name="Peer median"
            dataKey="peer"
            fill={PEER_COLOR}
            radius={[2, 2, 0, 0]}
            maxBarSize={44}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default BenchmarkChart;
