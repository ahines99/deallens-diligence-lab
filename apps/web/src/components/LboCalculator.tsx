"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { StatTile } from "@/components/ui/StatTile";
import { formatUsd, formatPct } from "@/lib/formatting";
import type { LboInputs, LboResult } from "@/lib/types";

const inputClass =
  "w-full rounded border border-line-strong bg-panel px-3 py-2 text-sm text-ink shadow-xs tabular-nums placeholder:text-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-ring/40";

const FIELDS: { key: keyof LboInputs; label: string; step: number; suffix: string }[] = [
  { key: "entry_multiple", label: "Entry EV / EBITDA", step: 0.5, suffix: "x" },
  { key: "exit_multiple", label: "Exit EV / EBITDA", step: 0.5, suffix: "x" },
  { key: "leverage", label: "Entry leverage (Debt / EBITDA)", step: 0.5, suffix: "x" },
  { key: "hold_years", label: "Hold period", step: 1, suffix: "yrs" },
  { key: "ebitda_cagr", label: "EBITDA CAGR", step: 0.01, suffix: "dec" },
];

function fmtMoic(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  return `${v.toFixed(2)}x`;
}

// Heatmap cell background: green tint for positive IRR (stronger = deeper),
// red tint for negative. Null cells stay blank.
function irrCellStyle(v: number | null): React.CSSProperties {
  if (v === null || v === undefined || Number.isNaN(v)) return {};
  if (v >= 0) {
    const a = Math.min(0.55, 0.06 + v * 1.4); // ~0.4 IRR saturates
    return { backgroundColor: `rgba(31, 160, 137, ${a.toFixed(3)})` };
  }
  const a = Math.min(0.5, 0.06 + Math.abs(v) * 1.4);
  return { backgroundColor: `rgba(184, 60, 42, ${a.toFixed(3)})` };
}

export function LboCalculator({
  workspaceId,
  initial,
}: {
  workspaceId: string;
  initial?: Partial<LboInputs>;
}) {
  const [inputs, setInputs] = useState<LboInputs>({
    entry_multiple: initial?.entry_multiple ?? 10,
    exit_multiple: initial?.exit_multiple ?? 10,
    leverage: initial?.leverage ?? 5,
    hold_years: initial?.hold_years ?? 5,
    ebitda_cagr: initial?.ebitda_cagr ?? 0.08,
  });
  const [result, setResult] = useState<LboResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setField(key: keyof LboInputs, raw: string) {
    const n = Number(raw);
    setInputs((prev) => ({ ...prev, [key]: Number.isNaN(n) ? prev[key] : n }));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.runLbo(workspaceId, inputs);
      setResult(res);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 404 ? "Workspace or EBITDA not available." : err.message);
      } else {
        setError("Failed to run the LBO model.");
      }
    } finally {
      setLoading(false);
    }
  }

  const sens = result?.sensitivity;

  return (
    <div className="space-y-5">
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label
                htmlFor={`lbo-${f.key}`}
                className="mb-1 block text-2xs font-semibold uppercase tracking-eyebrow text-muted"
              >
                {f.label}
              </label>
              <input
                id={`lbo-${f.key}`}
                type="number"
                step={f.step}
                value={inputs[f.key]}
                onChange={(e) => setField(f.key, e.target.value)}
                className={inputClass}
              />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={loading}>
            {loading ? (
              <>
                <span
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
                  aria-hidden
                />
                Running…
              </>
            ) : (
              "Run LBO"
            )}
          </Button>
          <span className="text-xs text-muted">
            EBITDA CAGR is a decimal (e.g. 0.08 = 8%). Debt is held flat; no interim FCF paydown.
          </span>
        </div>
      </form>

      {error && (
        <Callout tone="warning" title="Couldn't run the LBO">
          {error}
        </Callout>
      )}

      {result && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line shadow-panel sm:grid-cols-3 lg:grid-cols-6">
            {[
              { label: "IRR", value: result.irr === null ? "n/a" : formatPct(result.irr, 1), tone: "accent" as const },
              { label: "MOIC", value: fmtMoic(result.moic), tone: "accent" as const },
              { label: "Entry EV", value: result.entry_ev === null ? "n/a" : formatUsd(result.entry_ev) },
              { label: "Entry equity", value: result.entry_equity === null ? "n/a" : formatUsd(result.entry_equity) },
              { label: "Exit EV", value: result.exit_ev === null ? "n/a" : formatUsd(result.exit_ev) },
              { label: "Exit equity", value: result.exit_equity === null ? "n/a" : formatUsd(result.exit_equity) },
            ].map((t) => (
              <div key={t.label} className="bg-panel px-4 py-4">
                <StatTile label={t.label} value={t.value} tone={t.tone ?? "default"} />
              </div>
            ))}
          </div>

          {sens && sens.entry_multiples.length > 0 && (
            <div>
              <h4 className="eyebrow mb-2">IRR sensitivity — entry (rows) × exit (cols) EV/EBITDA</h4>
              <div className="-mx-5 overflow-x-auto px-5">
                <table className="border-collapse text-sm">
                  <thead>
                    <tr>
                      <th className="border-b-[1.5px] border-ink/80 py-2 pr-4 text-left text-2xs font-semibold uppercase tracking-eyebrow text-muted">
                        Entry \ Exit
                      </th>
                      {sens.exit_multiples.map((x) => (
                        <th
                          key={x}
                          className="border-b-[1.5px] border-ink/80 px-3 py-2 text-right text-2xs font-semibold uppercase tracking-eyebrow text-muted tabular-nums"
                        >
                          {x.toFixed(1)}x
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sens.entry_multiples.map((entry, ri) => (
                      <tr key={entry} className="border-b border-line-faint last:border-0">
                        <td className="py-1.5 pr-4 text-2xs font-semibold text-body tabular-nums">
                          {entry.toFixed(1)}x
                        </td>
                        {sens.exit_multiples.map((_, ci) => {
                          const v = sens.irr_grid[ri]?.[ci] ?? null;
                          return (
                            <td
                              key={ci}
                              style={irrCellStyle(v)}
                              className="px-3 py-1.5 text-right text-[0.8rem] tabular-nums text-ink"
                            >
                              {v === null ? "—" : formatPct(v, 0)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result.assumptions.length > 0 && (
            <Callout tone="muted" title="Assumptions">
              <ul className="list-disc space-y-1 pl-4">
                {result.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </Callout>
          )}
        </div>
      )}
    </div>
  );
}

export default LboCalculator;
