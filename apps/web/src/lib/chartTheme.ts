// DealLens chart theme. Categorical series validated with the dataviz palette validator
// (lightness band, chroma floor, CVD separation all PASS on the light chart surface).
// Assign categorical hues in FIXED order — never cycle, never dual-axis.

export const CHART_SERIES = ["#2E6FA8", "#1FA089", "#C98A2C", "#8A5CB0"] as const;

export const CHART = {
  grid: "#E6EBF0",
  axis: "#8DA0AE",
  axisTick: "#5E7284",
  accent: "#0B4F82", // deep brand blue for single-series / target marks
  gold: "#B0863C",
  muted: "#9DB0BE",
  surface: "#FFFFFF",
};

// Semantic assignments so the same metric keeps the same color across charts.
export const SERIES_COLOR: Record<string, string> = {
  gross_margin: "#2E6FA8",
  operating_margin: "#1FA089",
  net_margin: "#C98A2C",
  rnd_pct: "#8A5CB0",
  revenue: "#0B4F82",
  target: "#2E6FA8",
  peer: "#C98A2C",
};

// Shared Recharts style fragments.
export const tickStyle = { fill: CHART.axisTick, fontSize: 11 };
export const axisLineStyle = { stroke: CHART.axis };
