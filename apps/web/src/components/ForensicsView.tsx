import { Card } from "@/components/ui/Card";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { formatUsd, formatPct } from "@/lib/formatting";
import type { Forensics, ForensicRating, QoEMetric } from "@/lib/types";

const RATING_TONE: Record<ForensicRating, BadgeTone> = {
  strong: "green",
  neutral: "slate",
  weak: "amber",
  distress: "red",
  elevated: "red",
  "n/a": "neutral",
};

const RATING_LABEL: Record<ForensicRating, string> = {
  strong: "Strong",
  neutral: "Neutral",
  weak: "Weak",
  distress: "Distress",
  elevated: "Elevated",
  "n/a": "N/A",
};

function fmtScore(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}

function fmtQoE(unit: QoEMetric["unit"], v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  switch (unit) {
    case "pct":
      return formatPct(v, 1);
    case "x":
      return `${v.toFixed(2)}x`;
    case "usd":
      return formatUsd(v);
    case "days":
      return `${v.toFixed(0)} days`;
    case "ratio":
      return v.toFixed(2);
    default:
      return String(v);
  }
}

export function ForensicsView({ data }: { data: Forensics }) {
  return (
    <div className="space-y-6">
      <Callout tone="info" title="Deterministic, computed from filed XBRL">
        Altman Z″, Piotroski F, Beneish M, and the accruals ratio are computed from the target&apos;s
        stored SEC XBRL company facts. Where a required field was untagged, the metric degrades to n/a
        rather than being imputed. These are screening heuristics for human review — not investment advice.
      </Callout>

      <div className="grid gap-4 sm:grid-cols-2">
        {data.scores.map((s) => (
          <Card
            key={s.key}
            title={s.label}
            right={<Badge tone={RATING_TONE[s.rating] ?? "neutral"}>{RATING_LABEL[s.rating] ?? s.rating}</Badge>}
          >
            <div className="space-y-3">
              <div className="font-sans text-[2rem] font-semibold leading-none tabular-nums text-ink">
                {fmtScore(s.value)}
              </div>
              <p className="text-xs leading-relaxed text-muted">{s.interpretation}</p>
              {s.note && (
                <p className="text-2xs italic leading-snug text-faint">{s.note}</p>
              )}
              {s.components.length > 0 && (
                <div className="border-t border-line-faint pt-2">
                  <div className="eyebrow mb-1.5 text-faint">Components</div>
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                    {s.components.map((c) => (
                      <div key={c.name} className="flex items-baseline justify-between gap-2">
                        <dt className="truncate text-2xs text-muted">{c.name}</dt>
                        <dd className="tabular-nums text-2xs font-medium text-body">{fmtScore(c.value)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>

      <Card title="Quality-of-earnings metrics" subtitle="Working capital, cash conversion, coverage and leverage">
        <Table>
          <THead>
            <TR>
              <TH>Metric</TH>
              <TH align="right">Value</TH>
              <TH>Commentary</TH>
            </TR>
          </THead>
          <TBody>
            {data.qoe.map((m) => (
              <TR key={m.key} className="hover:bg-panel2">
                <TD className="font-medium text-ink">{m.label}</TD>
                <TD align="right" className="tabular-nums">
                  {fmtQoE(m.unit, m.value)}
                </TD>
                <TD className="max-w-md text-xs text-muted">{m.commentary}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Card>

      {data.notes.length > 0 && (
        <Callout tone="muted" title="Notes">
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

export default ForensicsView;
