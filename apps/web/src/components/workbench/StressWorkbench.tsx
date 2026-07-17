"use client";

import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useInvalidatedResult } from "@/lib/useInvalidatedResult";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable } from "@/components/ui/Table";
import { Callout } from "@/components/ui/Callout";
import {
  Field,
  InlineError,
  Metric,
  MetricStrip,
  SelectInput,
  TextArea,
  TextInput,
} from "./Primitives";
import type {
  CaseKey,
  ReverseStressResult,
  SensitivityResult,
  SensitivityVariable,
  UnderwritingCaseVersion,
  ValuationReference,
  ValuationTriangulationResult,
  WorkingCapitalObservation,
  WorkingCapitalPegResult,
} from "@/lib/types";

const money = (value: number | null) =>
  value === null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
        notation: Math.abs(value) > 1_000_000 ? "compact" : "standard",
      }).format(value);
const pct = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);
const multiple = (value: number | null) => (value === null ? "—" : `${value.toFixed(1)}x`);
const variables: { value: SensitivityVariable; label: string }[] = [
  { value: "entry_multiple", label: "Entry multiple" },
  { value: "exit_multiple", label: "Exit multiple" },
  { value: "base_rate_shift", label: "Base-rate shift" },
  { value: "revenue_growth_shift", label: "Revenue-growth shift" },
  { value: "ebitda_margin_shift", label: "EBITDA-margin shift" },
];

function parseValues(value: FormDataEntryValue | null) {
  return String(value || "")
    .split(",")
    .map((item) => Number(item.trim()))
    .filter(Number.isFinite);
}

function normalizeAxis(variable: SensitivityVariable, values: number[]) {
  return variable.includes("shift") ? values.map((value) => value / 100) : values;
}

function displayMetric(value: number | null, metric: string) {
  return metric === "irr" ? pct(value) : metric === "moic" ? multiple(value) : money(value);
}

function StaleResultNotice({ show }: { show: boolean }) {
  return show ? (
    <div className="mt-4">
      <Callout tone="muted" title="Inputs changed">
        The prior output was cleared. Run this analysis again for the current inputs.
      </Callout>
    </div>
  ) : null;
}

function SensitivityPanel({ workspaceId, model }: { workspaceId: string; model: UnderwritingCaseVersion }) {
  const { result, setFreshResult, invalidateResult, resultWasInvalidated } =
    useInvalidatedResult<SensitivityResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const rowVariable = String(data.get("row_variable")) as SensitivityVariable;
    const columnVariable = String(data.get("column_variable")) as SensitivityVariable;
    setBusy(true);
    setError(null);
    try {
      setFreshResult(
        await api.runSensitivity(workspaceId, {
          assumptions: model.assumptions,
          rows: {
            variable: rowVariable,
            values: normalizeAxis(rowVariable, parseValues(data.get("row_values"))),
          },
          columns: {
            variable: columnVariable,
            values: normalizeAxis(columnVariable, parseValues(data.get("column_values"))),
          },
          metric: String(data.get("metric")) as "irr" | "moic" | "minimum_liquidity",
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sensitivity could not run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      eyebrow="Two-variable sensitivity"
      title="Returns and liquidity matrix"
      subtitle="Shift variables are entered as percentage points."
    >
      <form
        onSubmit={submit}
        onChange={invalidateResult}
        className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1.3fr_1fr_1.3fr_1fr_auto] xl:items-end"
      >
        <Field label="Row variable">
          <SelectInput name="row_variable" defaultValue="exit_multiple">
            {variables.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </SelectInput>
        </Field>
        <Field label="Row values"><TextInput name="row_values" defaultValue="8, 9, 10, 11, 12" required /></Field>
        <Field label="Column variable">
          <SelectInput name="column_variable" defaultValue="entry_multiple">
            {variables.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </SelectInput>
        </Field>
        <Field label="Column values"><TextInput name="column_values" defaultValue="8, 9, 10, 11, 12" required /></Field>
        <Field label="Metric">
          <SelectInput name="metric"><option value="irr">IRR</option><option value="moic">MOIC</option><option value="minimum_liquidity">Minimum liquidity</option></SelectInput>
        </Field>
        <Button type="submit" disabled={busy}>{busy ? "Running…" : "Run matrix"}</Button>
        <div className="md:col-span-2 xl:col-span-6"><InlineError message={error} /></div>
      </form>
      <StaleResultNotice show={resultWasInvalidated && !result} />
      {result && (
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-full border-collapse text-xs">
            {/* Axis values may repeat (the API accepts duplicate points), so keys include the index. */}
            <thead><tr><th className="border border-line bg-panel2 px-3 py-2 text-left text-muted">{result.row_variable} ↓ / {result.column_variable} →</th>{result.column_values.map((value, columnIndex) => <th key={`${value}-${columnIndex}`} className="border border-line bg-panel2 px-3 py-2 text-right text-muted">{result.column_variable.includes("shift") ? pct(value) : value.toFixed(1)}</th>)}</tr></thead>
            <tbody>{result.row_values.map((row, rowIndex) => <tr key={`${row}-${rowIndex}`}><th className="border border-line bg-panel2 px-3 py-2 text-left text-muted">{result.row_variable.includes("shift") ? pct(row) : row.toFixed(1)}</th>{result.grid[rowIndex].map((value, columnIndex) => <td key={columnIndex} className="border border-line px-3 py-2 text-right tabular-nums">{displayMetric(value, result.metric)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ReverseStressPanel({ workspaceId, model }: { workspaceId: string; model: UnderwritingCaseVersion }) {
  const { result, setFreshResult, invalidateResult, resultWasInvalidated } =
    useInvalidatedResult<ReverseStressResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const variable = String(data.get("variable")) as SensitivityVariable;
    const objective = String(data.get("objective")) as "irr" | "moic" | "minimum_liquidity";
    const variableScale = variable.includes("shift") ? 0.01 : 1;
    setBusy(true);
    setError(null);
    try {
      setFreshResult(await api.runReverseStress(workspaceId, {
        assumptions: model.assumptions,
        variable,
        objective,
        target: Number(data.get("target")) * (objective === "irr" ? 0.01 : 1),
        lower_bound: Number(data.get("lower_bound")) * variableScale,
        upper_bound: Number(data.get("upper_bound")) * variableScale,
      }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reverse stress could not run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card eyebrow="Reverse stress" title="Solve for the break point" subtitle="Find the driver value at which the selected return or liquidity objective is reached.">
      <form onSubmit={submit} onChange={invalidateResult} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6 xl:items-end">
        <Field label="Variable"><SelectInput name="variable" defaultValue="exit_multiple">{variables.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</SelectInput></Field>
        <Field label="Objective"><SelectInput name="objective"><option value="irr">IRR</option><option value="moic">MOIC</option><option value="minimum_liquidity">Minimum liquidity</option></SelectInput></Field>
        <Field label="Target" hint="IRR as %"><TextInput name="target" type="number" step="0.1" defaultValue="20" /></Field>
        <Field label="Lower bound"><TextInput name="lower_bound" type="number" step="0.1" defaultValue="5" /></Field>
        <Field label="Upper bound"><TextInput name="upper_bound" type="number" step="0.1" defaultValue="15" /></Field>
        <Button type="submit" disabled={busy}>{busy ? "Solving…" : "Solve"}</Button>
        <div className="sm:col-span-2 xl:col-span-6"><InlineError message={error} /></div>
      </form>
      <StaleResultNotice show={resultWasInvalidated && !result} />
      {result && <div className="mt-5"><MetricStrip columns={4}><Metric label="Status" value={result.status === "solved" ? "Solved" : "No solution"} tone={result.status === "solved" ? "positive" : "warning"} /><Metric label="Solved driver" value={result.solved_value === null ? "—" : result.variable.includes("shift") ? pct(result.solved_value) : result.solved_value.toFixed(2)} /><Metric label="Achieved objective" value={displayMetric(result.achieved_value, result.objective)} /><Metric label="Iterations" value={result.iterations} detail={`${result.lower_value ?? "—"} to ${result.upper_value ?? "—"}`} /></MetricStrip></div>}
    </Card>
  );
}

function WorkingCapitalPanel({ workspaceId }: { workspaceId: string }) {
  const { result, setFreshResult, invalidateResult, resultWasInvalidated } =
    useInvalidatedResult<WorkingCapitalPegResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const observations: WorkingCapitalObservation[] = String(data.get("observations") || "")
        .split(/\r?\n/)
        .filter(Boolean)
        .map((line) => {
          const row = line.split(",").map((value) => value.trim());
          return {
            observation_date: row[0], accounts_receivable: Number(row[1] || 0), inventory: Number(row[2] || 0),
            other_operating_current_assets: Number(row[3] || 0), accounts_payable: Number(row[4] || 0),
            accrued_liabilities: Number(row[5] || 0), deferred_revenue: Number(row[6] || 0),
            other_operating_current_liabilities: Number(row[7] || 0), excluded_net_amount: Number(row[8] || 0),
          };
        });
      setFreshResult(await api.calculateWorkingCapitalPeg(workspaceId, {
        observations,
        closing_date: String(data.get("closing_date")),
        method: String(data.get("method")) as "median_ltm" | "average_ltm" | "seasonal_average",
        delivered_working_capital: data.get("delivered") ? Number(data.get("delivered")) : null,
      }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Working-capital peg could not run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card eyebrow="Working capital" title="Normalized peg & closing true-up" subtitle="CSV rows: date, AR, inventory, other current assets, AP, accruals, deferred revenue, other current liabilities, excluded net amount.">
      <form onSubmit={submit} onChange={invalidateResult} className="space-y-3">
        <Field label="Monthly observations" hint="CSV rows"><TextArea name="observations" rows={6} required placeholder={"2025-01-31,1200,300,50,700,180,100,25,0\n2025-02-28,1250,310,45,720,185,105,25,0"} className="font-mono text-xs" /></Field>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 lg:items-end">
          <Field label="Closing date"><TextInput name="closing_date" type="date" required /></Field>
          <Field label="Method"><SelectInput name="method"><option value="median_ltm">Median LTM</option><option value="average_ltm">Average LTM</option><option value="seasonal_average">Seasonal average</option></SelectInput></Field>
          <Field label="Delivered NWC"><TextInput name="delivered" type="number" step="0.01" placeholder="Optional" /></Field>
          <Button type="submit" disabled={busy}>{busy ? "Calculating…" : "Calculate peg"}</Button>
        </div>
        <InlineError message={error} />
      </form>
      <StaleResultNotice show={resultWasInvalidated && !result} />
      {result && <div className="mt-5"><MetricStrip columns={5}><Metric label="Recommended peg" value={money(result.peg)} /><Metric label="Trailing average" value={money(result.trailing_average)} /><Metric label="Trailing median" value={money(result.trailing_median)} /><Metric label="Observed range" value={`${money(result.low)} – ${money(result.high)}`} /><Metric label="Price adjustment" value={money(result.purchase_price_adjustment)} tone={(result.purchase_price_adjustment ?? 0) < 0 ? "negative" : "positive"} /></MetricStrip></div>}
    </Card>
  );
}

function parseReferences(value: FormDataEntryValue | null): ValuationReference[] {
  return String(value || "").split(/\r?\n/).filter(Boolean).map((line) => {
    const [name, rawMultiple, source, asOfDate, evidenceRef] = line.split("|").map((item) => item.trim());
    return { name, ev_ebitda_multiple: Number(rawMultiple), source, as_of_date: asOfDate || null, evidence_ref: evidenceRef || null };
  });
}

function ValuationPanel({ workspaceId, model }: { workspaceId: string; model: UnderwritingCaseVersion }) {
  const { result, setFreshResult, invalidateResult, resultWasInvalidated } =
    useInvalidatedResult<ValuationTriangulationResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastPeriod = model.result.projection.at(-1);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      setFreshResult(await api.triangulateValuation(workspaceId, {
        ebitda: Number(data.get("ebitda")), net_debt: Number(data.get("net_debt")),
        dcf_enterprise_value: Number(data.get("dcf_ev")) || null,
        public_comps: parseReferences(data.get("public_comps")),
        precedent_transactions: parseReferences(data.get("precedents")),
        dcf_weight: Number(data.get("dcf_weight")) / 100,
        public_comps_weight: Number(data.get("comps_weight")) / 100,
        precedents_weight: Number(data.get("precedents_weight")) / 100,
      }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Valuation could not run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card eyebrow="Valuation triangulation" title="DCF, public comps & precedent transactions" subtitle="Manual or licensed references retain their source, as-of date, and evidence reference.">
      <form onSubmit={submit} onChange={invalidateResult} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><Field label="EBITDA"><TextInput name="ebitda" type="number" step="0.01" min="0.01" defaultValue={lastPeriod?.ebitda} required /></Field><Field label="Net debt"><TextInput name="net_debt" type="number" step="0.01" defaultValue={lastPeriod?.net_debt ?? 0} /></Field><Field label="DCF enterprise value"><TextInput name="dcf_ev" type="number" step="0.01" min="0" defaultValue={model.result.dcf.enterprise_value} /></Field></div>
        <div className="grid gap-3 lg:grid-cols-2"><Field label="Public comps" hint="Name | multiple | source | date | evidence"><TextArea name="public_comps" rows={4} className="font-mono text-xs" placeholder="Peer Co | 10.5 | Licensed comps set | 2026-06-30 | E-102" /></Field><Field label="Precedent transactions" hint="Name | multiple | source | date | evidence"><TextArea name="precedents" rows={4} className="font-mono text-xs" placeholder="Target / Buyer | 11.2 | Deal announcement | 2025-11-14 | E-114" /></Field></div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 lg:items-end"><Field label="DCF weight %"><TextInput name="dcf_weight" type="number" min="0" max="100" defaultValue="40" /></Field><Field label="Comps weight %"><TextInput name="comps_weight" type="number" min="0" max="100" defaultValue="35" /></Field><Field label="Precedents weight %"><TextInput name="precedents_weight" type="number" min="0" max="100" defaultValue="25" /></Field><Button type="submit" disabled={busy}>{busy ? "Triangulating…" : "Triangulate"}</Button></div>
        <InlineError message={error} />
      </form>
      <StaleResultNotice show={resultWasInvalidated && !result} />
      {result && <div className="mt-5 space-y-4"><MetricStrip columns={4}><Metric label="Blended EV" value={money(result.blended_enterprise_value)} /><Metric label="Blended equity" value={money(result.blended_equity_value)} tone="positive" /><Metric label="Valuation low" value={money(result.valuation_low)} /><Metric label="Valuation high" value={money(result.valuation_high)} /></MetricStrip><DataTable rows={result.methods} getRowKey={(row) => row.method} columns={[{ key: "method", header: "Method", render: (row) => <Badge tone="indigo">{row.method.replaceAll("_", " ")}</Badge> }, { key: "refs", header: "References", align: "right", render: (row) => row.reference_count }, { key: "range", header: "Multiple range", align: "right", render: (row) => row.multiple_low === null ? "—" : `${multiple(row.multiple_low)} – ${multiple(row.multiple_high)}` }, { key: "ev", header: "Median EV", align: "right", render: (row) => money(row.enterprise_value_median) }, { key: "weight", header: "Normalized weight", align: "right", render: (row) => pct(row.normalized_weight) }]} />{result.warnings.length > 0 && <Callout tone="warning">{result.warnings.join(" ")}</Callout>}</div>}
    </Card>
  );
}

export function StressWorkbench({ workspaceId, cases }: { workspaceId: string; cases: UnderwritingCaseVersion[] }) {
  const [caseKey, setCaseKey] = useState<CaseKey>("base");
  const model = useMemo(() => cases.find((item) => item.case_key === caseKey) ?? cases[0], [cases, caseKey]);
  if (!model) return <Callout tone="muted" title="Cases required">Build the base, upside, and downside case set before running valuation or stress tests.</Callout>;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel px-4 py-3"><div><div className="eyebrow">Active case</div><p className="mt-0.5 text-xs text-muted">Every analysis below is tied to this exact saved case version. Switching cases clears unsaved outputs.</p></div><div className="flex gap-1">{cases.map((item) => <button key={item.id} onClick={() => setCaseKey(item.case_key)} className={`rounded px-3 py-1.5 text-xs font-semibold capitalize ${model.id === item.id ? "bg-accent text-white" : "bg-panel2 text-muted hover:text-ink"}`}>{item.case_key} · v{item.version}</button>)}</div></div>
      <ValuationPanel key={`valuation-${model.id}`} workspaceId={workspaceId} model={model} />
      <SensitivityPanel key={`sensitivity-${model.id}`} workspaceId={workspaceId} model={model} />
      <ReverseStressPanel key={`reverse-${model.id}`} workspaceId={workspaceId} model={model} />
      <WorkingCapitalPanel key={`working-capital-${model.id}`} workspaceId={workspaceId} />
    </div>
  );
}
