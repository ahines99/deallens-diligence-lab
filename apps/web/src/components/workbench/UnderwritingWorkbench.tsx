"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Callout } from "@/components/ui/Callout";
import { DataTable } from "@/components/ui/Table";
import { EmptyPanel, Field, InlineError, Metric, MetricStrip, TextArea, TextInput } from "./Primitives";
import type { CaseKey, OperatingPeriodAssumption, UnderwritingAssumptions, UnderwritingCaseVersion } from "@/lib/types";
import { useActor } from "@/components/identity/ActorContext";
import { underwritingDecisionPermissions } from "./underwritingGovernance";
import { savedUnderwritingControlValues } from "./underwritingHydration";

const CASES: CaseKey[] = ["base", "upside", "downside"];
const pct = (value: number | null, digits = 1) => value === null ? "—" : `${(value * 100).toFixed(digits)}%`;
const multiple = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}x`;
const money = (value: number | null, currency = "USD") => value === null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0, notation: Math.abs(value) >= 1_000_000 ? "compact" : "standard" }).format(value);
const tone: Record<CaseKey, BadgeTone> = { base: "indigo", upside: "green", downside: "red" };
const num = (data: FormData, key: string, fallback = 0) => { const value = Number(data.get(key)); return Number.isFinite(value) ? value : fallback; };
const rate = (data: FormData, key: string, fallback = 0) => num(data, key, fallback * 100) / 100;

function projectionPeriods(drivers: UnderwritingAssumptions["projection"]["default_drivers"]): OperatingPeriodAssumption[] {
  const periods: OperatingPeriodAssumption[] = [];
  for (let month = 1; month <= 24; month += 1) periods.push({ label: `M${String(month).padStart(2, "0")}`, months: 1 });
  for (let year = 3; year <= 5; year += 1) periods.push({ label: `Y${year}`, months: 12 });
  return periods.map((period) => ({ ...period, annual_revenue_growth: drivers.annual_revenue_growth, ebitda_margin: drivers.ebitda_margin }));
}

function buildAssumptions(data: FormData, key: CaseKey): UnderwritingAssumptions {
  const growthShift = key === "upside" ? rate(data, "up_growth_delta", .02) : key === "downside" ? rate(data, "down_growth_delta", -.03) : 0;
  const marginShift = key === "upside" ? rate(data, "up_margin_delta", .02) : key === "downside" ? rate(data, "down_margin_delta", -.03) : 0;
  const exitShift = key === "upside" ? num(data, "up_exit_delta", .5) : key === "downside" ? num(data, "down_exit_delta", -1) : 0;
  const ltmEbitda = num(data, "ltm_ebitda");
  const drivers = {
    annual_revenue_growth: rate(data, "growth", .08) + growthShift,
    gross_margin: rate(data, "gross_margin", .6),
    ebitda_margin: rate(data, "ebitda_margin", .2) + marginShift,
    da_percent_revenue: rate(data, "da_pct", .03), capex_percent_revenue: rate(data, "capex_pct", .04),
    net_working_capital_percent_revenue: rate(data, "nwc_pct", .1), cash_tax_rate: rate(data, "tax_rate", .25), base_rate: rate(data, "base_rate", .04),
  };
  const leverage = num(data, "leverage", 4.5);
  const minCash = num(data, "minimum_cash");
  return {
    currency: "USD",
    historical: { ltm_revenue: num(data, "ltm_revenue"), ltm_ebitda: ltmEbitda, starting_cash: num(data, "starting_cash"), starting_net_working_capital: num(data, "starting_nwc"), existing_debt: num(data, "existing_debt") },
    transaction: { close_date: String(data.get("close_date")), entry_multiple: num(data, "entry_multiple", 10), exit_multiple: num(data, "exit_multiple", 10) + exitShift, hold_period_years: 5, transaction_fees: num(data, "transaction_fees"), management_options_cashout: num(data, "options_cashout"), other_uses: 0, seller_rollover: num(data, "seller_rollover"), minimum_cash: minCash, cash_sweep_percent: rate(data, "cash_sweep", 1) },
    projection: { default_drivers: drivers, periods: projectionPeriods(drivers) },
    debt_tranches: [
      { name: "Senior term loan", tranche_type: "term_loan", initial_amount: ltmEbitda * leverage, senior: true, spread: rate(data, "term_spread", .055), base_rate_floor: rate(data, "rate_floor", .01), pik_rate: 0, annual_amortization_rate: rate(data, "amortization", .01), cash_sweep_priority: 1, sweep_eligible: true, oid_discount: rate(data, "oid", .01), financing_fee_percent: rate(data, "financing_fee", .02) },
      { name: "Revolver", tranche_type: "revolver", initial_amount: 0, commitment: num(data, "revolver_commitment", minCash * 2), senior: true, spread: rate(data, "revolver_spread", .045), base_rate_floor: rate(data, "rate_floor", .01), pik_rate: 0, annual_amortization_rate: 0, cash_sweep_priority: 0, sweep_eligible: true, oid_discount: 0, financing_fee_percent: 0 },
    ],
    covenants: [
      { name: "Maximum total leverage", metric: "total_leverage", test: "maximum", threshold: num(data, "max_leverage", 6), threshold_by_period: {} },
      { name: "Minimum liquidity", metric: "minimum_liquidity", test: "minimum", threshold: minCash, threshold_by_period: {} },
      { name: "Minimum interest coverage", metric: "interest_coverage", test: "minimum", threshold: num(data, "min_interest_coverage", 1.5), threshold_by_period: {} },
    ],
    valuation: { discount_rate: rate(data, "discount_rate", .12), terminal_growth_rate: rate(data, "terminal_growth", .025), mid_year_convention: true },
  };
}

function CaseBuilder({ workspaceId, cases, onModelInputChange }: { workspaceId: string; cases: UnderwritingCaseVersion[]; onModelInputChange?: () => void }) {
  const { actor } = useActor();
  const formRef = useRef<HTMLFormElement>(null);
  const savedControls = useMemo(() => savedUnderwritingControlValues(cases), [cases]);
  const router = useRouter(); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const notifyModelInputChange = onModelInputChange ?? (() => {
    window.dispatchEvent(new CustomEvent("deallens:underwriting-input-dirty", { detail: { workspaceId } }));
  });
  useEffect(() => {
    const form = formRef.current;
    if (!form) return;
    Object.entries(savedControls).forEach(([name, value]) => {
      const control = form.elements.namedItem(name);
      if (control instanceof HTMLInputElement || control instanceof HTMLSelectElement) {
        if (control instanceof HTMLInputElement) control.defaultValue = value;
        control.value = value;
      }
    });
  }, [savedControls]);
  const base = cases.find((x) => x.case_key === "base")?.assumptions;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy(true); setError(null);
    try {
      await api.createUnderwritingCaseSet(workspaceId, CASES.map((caseKey) => ({
        case_key: caseKey, label: `${caseKey[0].toUpperCase()}${caseKey.slice(1)} case`, assumptions: buildAssumptions(data, caseKey),
        expected_parent_version: cases.find((x) => x.case_key === caseKey)?.version, created_by: actor.actorId ?? "unattributed", change_note: String(data.get("change_note") || "Updated operating and transaction assumptions"),
      })));
      router.refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Could not calculate the case set."); } finally { setBusy(false); }
  }
  const h = base?.historical; const t = base?.transaction; const d = base?.projection.default_drivers; const debt = base?.debt_tranches.find((x) => x.tranche_type === "term_loan");
  return (
    <form
      ref={formRef}
      onSubmit={submit}
      onChange={(event) => {
        const field = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
        if (field.name && field.name !== "change_note") notifyModelInputChange();
      }}
      className="space-y-5"
    >
      <div className="grid gap-5 xl:grid-cols-3">
        <fieldset className="space-y-3"><legend className="mb-2 text-xs font-semibold text-ink">Historical baseline</legend><div className="grid grid-cols-2 gap-3"><Field label="LTM revenue"><TextInput name="ltm_revenue" type="number" step="0.01" min="0.01" required defaultValue={h?.ltm_revenue} /></Field><Field label="LTM EBITDA"><TextInput name="ltm_ebitda" type="number" step="0.01" required defaultValue={h?.ltm_ebitda} /></Field><Field label="Starting cash"><TextInput name="starting_cash" type="number" step="0.01" min="0" defaultValue={h?.starting_cash ?? 0} /></Field><Field label="Existing debt"><TextInput name="existing_debt" type="number" step="0.01" min="0" defaultValue={h?.existing_debt ?? 0} /></Field><Field label="Starting NWC"><TextInput name="starting_nwc" type="number" step="0.01" defaultValue={h?.starting_net_working_capital ?? 0} /></Field></div></fieldset>
        <fieldset className="space-y-3"><legend className="mb-2 text-xs font-semibold text-ink">Transaction</legend><div className="grid grid-cols-2 gap-3"><Field label="Close date"><TextInput name="close_date" type="date" required defaultValue={t?.close_date ?? new Date().toISOString().slice(0, 10)} /></Field><Field label="Entry multiple"><TextInput name="entry_multiple" type="number" min="0.1" step="0.1" defaultValue={t?.entry_multiple ?? 10} /></Field><Field label="Exit multiple"><TextInput name="exit_multiple" type="number" min="0.1" step="0.1" defaultValue={t?.exit_multiple ?? 10} /></Field><Field label="Entry leverage"><TextInput name="leverage" type="number" min="0" step="0.1" defaultValue={debt && h?.ltm_ebitda ? debt.initial_amount / h.ltm_ebitda : 4.5} /></Field><Field label="Transaction fees"><TextInput name="transaction_fees" type="number" min="0" step="0.01" defaultValue={t?.transaction_fees ?? 0} /></Field><Field label="Seller rollover"><TextInput name="seller_rollover" type="number" min="0" step="0.01" defaultValue={t?.seller_rollover ?? 0} /></Field><Field label="Options cashout"><TextInput name="options_cashout" type="number" min="0" step="0.01" defaultValue={t?.management_options_cashout ?? 0} /></Field><Field label="Minimum cash"><TextInput name="minimum_cash" type="number" min="0" step="0.01" defaultValue={t?.minimum_cash ?? 0} /></Field></div></fieldset>
        <fieldset className="space-y-3"><legend className="mb-2 text-xs font-semibold text-ink">Operating drivers</legend><div className="grid grid-cols-2 gap-3"><Field label="Revenue growth"><TextInput name="growth" type="number" step="0.1" defaultValue={(d?.annual_revenue_growth ?? .08) * 100} /></Field><Field label="Gross margin"><TextInput name="gross_margin" type="number" step="0.1" defaultValue={(d?.gross_margin ?? .6) * 100} /></Field><Field label="EBITDA margin"><TextInput name="ebitda_margin" type="number" step="0.1" defaultValue={(d?.ebitda_margin ?? .2) * 100} /></Field><Field label="Capex / revenue"><TextInput name="capex_pct" type="number" step="0.1" defaultValue={(d?.capex_percent_revenue ?? .04) * 100} /></Field><Field label="NWC / revenue"><TextInput name="nwc_pct" type="number" step="0.1" defaultValue={(d?.net_working_capital_percent_revenue ?? .1) * 100} /></Field><Field label="Cash tax rate"><TextInput name="tax_rate" type="number" step="0.1" defaultValue={(d?.cash_tax_rate ?? .25) * 100} /></Field><Field label="D&A / revenue"><TextInput name="da_pct" type="number" step="0.1" defaultValue={(d?.da_percent_revenue ?? .03) * 100} /></Field><Field label="Base rate"><TextInput name="base_rate" type="number" step="0.1" defaultValue={(d?.base_rate ?? .04) * 100} /></Field></div></fieldset>
      </div>
      <details className="rounded-md border border-line bg-panel2 px-4 py-3"><summary className="cursor-pointer text-xs font-semibold text-ink">Debt, covenant, valuation, and scenario controls</summary><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6"><Field label="Term spread %"><TextInput name="term_spread" type="number" step="0.1" defaultValue={(debt?.spread ?? .055) * 100} /></Field><Field label="Revolver spread %"><TextInput name="revolver_spread" type="number" step="0.1" defaultValue="4.5" /></Field><Field label="Rate floor %"><TextInput name="rate_floor" type="number" step="0.1" defaultValue={(debt?.base_rate_floor ?? .01) * 100} /></Field><Field label="Amortization %"><TextInput name="amortization" type="number" step="0.1" defaultValue={(debt?.annual_amortization_rate ?? .01) * 100} /></Field><Field label="OID %"><TextInput name="oid" type="number" step="0.1" defaultValue={(debt?.oid_discount ?? .01) * 100} /></Field><Field label="Financing fee %"><TextInput name="financing_fee" type="number" step="0.1" defaultValue={(debt?.financing_fee_percent ?? .02) * 100} /></Field><Field label="Revolver commitment"><TextInput name="revolver_commitment" type="number" min="0" defaultValue={base?.debt_tranches.find((x) => x.tranche_type === "revolver")?.commitment ?? 0} /></Field><Field label="Cash sweep %"><TextInput name="cash_sweep" type="number" step="1" defaultValue={(t?.cash_sweep_percent ?? 1) * 100} /></Field><Field label="Max leverage"><TextInput name="max_leverage" type="number" step="0.1" defaultValue="6" /></Field><Field label="Min interest coverage"><TextInput name="min_interest_coverage" type="number" step="0.1" defaultValue="1.5" /></Field><Field label="Discount rate %"><TextInput name="discount_rate" type="number" step="0.1" defaultValue={(base?.valuation.discount_rate ?? .12) * 100} /></Field><Field label="Terminal growth %"><TextInput name="terminal_growth" type="number" step="0.1" defaultValue={(base?.valuation.terminal_growth_rate ?? .025) * 100} /></Field><Field label="Upside growth Δ %"><TextInput name="up_growth_delta" type="number" step="0.1" defaultValue="2" /></Field><Field label="Upside margin Δ %"><TextInput name="up_margin_delta" type="number" step="0.1" defaultValue="2" /></Field><Field label="Upside exit Δ"><TextInput name="up_exit_delta" type="number" step="0.1" defaultValue="0.5" /></Field><Field label="Downside growth Δ %"><TextInput name="down_growth_delta" type="number" step="0.1" defaultValue="-3" /></Field><Field label="Downside margin Δ %"><TextInput name="down_margin_delta" type="number" step="0.1" defaultValue="-3" /></Field><Field label="Downside exit Δ"><TextInput name="down_exit_delta" type="number" step="0.1" defaultValue="-1" /></Field></div></details>
      <Field label="Change note"><TextArea name="change_note" rows={2} placeholder="What changed and why" /></Field>
      <div className="flex flex-wrap items-center gap-3"><Button type="submit" disabled={busy}>{busy ? "Calculating three cases…" : cases.length ? "Create new case versions" : "Build base / upside / downside"}</Button><span className="text-2xs text-faint">Creates an immutable, hash-addressed version of each case.</span><InlineError message={error} /></div>
    </form>
  );
}

function DecisionActions({ workspaceId, model }: { workspaceId: string; model: UnderwritingCaseVersion }) {
  const { actor, profile } = useActor();
  const router = useRouter(); const [busy, setBusy] = useState(false); const [error,setError]=useState<string|null>(null);
  const permissions=underwritingDecisionPermissions(model.latest_decision,actor.actorId);
  async function decide(decision: "submitted" | "approved" | "rejected") { if(!actor.actorId)return;setBusy(true);setError(null);try { await api.decideUnderwritingCase(workspaceId, model.case_key, model.version, decision, actor.actorId, `${profile.name} recorded the ${decision} model-review decision`); router.refresh(); } catch(caught){setError(caught instanceof ApiError?caught.message:"Could not record the underwriting decision.");} finally { setBusy(false); } }
  return <div className="max-w-md"><div className="flex flex-wrap justify-end gap-1"><Button variant="secondary" onClick={() => void decide("submitted")} disabled={busy||!permissions.canSubmit}>Submit</Button><Button variant="ghost" onClick={() => void decide("approved")} disabled={busy||!permissions.canReview}>Approve</Button><Button variant="ghost" onClick={() => void decide("rejected")} disabled={busy||!permissions.canReview}>Reject</Button></div>{permissions.isSubmitter&&<div className="mt-2"><Callout tone="warning" title="Independent review required">{profile.name} submitted this case and cannot approve or reject it. Switch actors in the header for four-eyes review.</Callout></div>}{permissions.needsSubmission&&<p className="mt-1 text-right text-2xs text-faint">Submit this version before a different actor reviews it.</p>}{permissions.isFinal&&<p className="mt-1 text-right text-2xs text-faint">This version has a final decision; create a new version to revise it.</p>}<InlineError message={error}/></div>;
}

function CaseResults({ workspaceId, model }: { workspaceId: string; model: UnderwritingCaseVersion }) {
  const r = model.result; const last = r.projection.at(-1); const first = r.projection[0];
  return <div className="space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><Badge tone={tone[model.case_key]}>{model.case_key}</Badge><span className="text-xs text-muted">Version {model.version} · {model.input_hash.slice(0, 10)}…</span>{model.latest_decision && <Badge tone={model.latest_decision.decision === "approved" ? "green" : model.latest_decision.decision === "rejected" ? "red" : "amber"}>{model.latest_decision.decision}</Badge>}</div><DecisionActions workspaceId={workspaceId} model={model} /></div>
    <MetricStrip columns={6}><Metric label="Entry EV" value={money(r.sources_uses.entry_enterprise_value, r.currency)} detail={`${model.assumptions.transaction.entry_multiple.toFixed(1)}x entry`} /><Metric label="Sponsor equity" value={money(r.sources_uses.sponsor_equity, r.currency)} detail={pct(r.sources_uses.sponsor_ownership)} /><Metric label="MOIC" value={multiple(r.returns.moic)} tone={(r.returns.moic ?? 0) >= 2 ? "positive" : "warning"} /><Metric label="XIRR" value={pct(r.returns.xirr)} tone={(r.returns.xirr ?? 0) >= .2 ? "positive" : "warning"} /><Metric label="Min. liquidity" value={money(r.summary.minimum_liquidity, r.currency)} tone={r.summary.minimum_liquidity < 0 ? "negative" : "default"} /><Metric label="Max leverage" value={multiple(r.summary.maximum_total_leverage)} detail={r.summary.first_covenant_breach ? `Breach: ${r.summary.first_covenant_breach}` : "No modeled breach"} tone={r.summary.first_covenant_breach ? "negative" : "positive"} /></MetricStrip>
    <div className="grid gap-6 xl:grid-cols-2"><Card eyebrow="Transaction" title="Sources & uses"><div className="grid grid-cols-2 gap-6"><div><div className="mb-2 text-2xs font-semibold uppercase tracking-eyebrow text-muted">Uses</div>{r.sources_uses.uses.map((x) => <div key={x.name} className="flex justify-between border-b border-line-faint py-1.5 text-xs"><span>{x.name}</span><span>{money(x.amount, r.currency)}</span></div>)}<div className="flex justify-between pt-2 text-xs font-semibold text-ink"><span>Total uses</span><span>{money(r.sources_uses.total_uses, r.currency)}</span></div></div><div><div className="mb-2 text-2xs font-semibold uppercase tracking-eyebrow text-muted">Sources</div>{r.sources_uses.sources.map((x) => <div key={x.name} className="flex justify-between border-b border-line-faint py-1.5 text-xs"><span>{x.name}</span><span>{money(x.amount, r.currency)}</span></div>)}<div className="flex justify-between pt-2 text-xs font-semibold text-ink"><span>Total sources</span><span>{money(r.sources_uses.total_sources, r.currency)}</span></div></div></div></Card><Card eyebrow="Valuation" title="FCFF DCF"><div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs"><div><span className="text-muted">Enterprise value</span><div className="mt-0.5 text-lg font-semibold text-ink">{money(r.dcf.enterprise_value, r.currency)}</div></div><div><span className="text-muted">Equity value</span><div className="mt-0.5 text-lg font-semibold text-ink">{money(r.dcf.equity_value, r.currency)}</div></div><div className="flex justify-between border-t border-line pt-2"><span>PV explicit FCFF</span><span>{money(r.dcf.pv_explicit_fcff, r.currency)}</span></div><div className="flex justify-between border-t border-line pt-2"><span>PV terminal value</span><span>{money(r.dcf.pv_terminal_value, r.currency)}</span></div><div className="flex justify-between"><span>Discount rate</span><span>{pct(r.dcf.discount_rate)}</span></div><div className="flex justify-between"><span>Terminal value / EV</span><span>{pct(r.dcf.terminal_value_percent)}</span></div></div></Card></div>
    <Card eyebrow="Integrated model" title="Operating, cash flow, debt & covenant schedule" subtitle="Monthly for the first 24 months, then annual through year five."><DataTable rows={r.projection} getRowKey={(row) => row.label} columns={[
      { key: "period", header: "Period", render: (row) => <span className="font-medium text-ink">{row.label}</span> }, { key: "revenue", header: "Revenue", align: "right", render: (row) => money(row.revenue, r.currency) }, { key: "ebitda", header: "EBITDA", align: "right", render: (row) => money(row.ebitda, r.currency) }, { key: "margin", header: "Margin", align: "right", render: (row) => pct(row.ebitda_margin) }, { key: "fcff", header: "FCFF", align: "right", render: (row) => money(row.fcff, r.currency) }, { key: "debt", header: "Debt", align: "right", render: (row) => money(row.total_debt, r.currency) }, { key: "lev", header: "Leverage", align: "right", render: (row) => multiple(row.total_leverage) }, { key: "liq", header: "Liquidity", align: "right", render: (row) => <span className={row.liquidity_shortfall > 0 ? "text-negative" : ""}>{money(row.liquidity, r.currency)}</span> }, { key: "covenant", header: "Covenants", render: (row) => row.covenants.some((x) => x.passed === false) ? <Badge tone="red">Breach</Badge> : <Badge tone="green">Pass</Badge> },
    ]} /></Card>
    {last && <Card eyebrow="Debt workbench" title="Exit debt and covenant position"><DataTable rows={last.debt_tranches} getRowKey={(row) => row.name} columns={[{ key: "name", header: "Tranche", render: (row) => row.name }, { key: "open", header: "Opening", align: "right", render: (row) => money(row.opening_balance, r.currency) }, { key: "rate", header: "Cash rate", align: "right", render: (row) => pct(row.cash_rate) }, { key: "interest", header: "Cash interest", align: "right", render: (row) => money(row.cash_interest, r.currency) }, { key: "amort", header: "Amortization", align: "right", render: (row) => money(row.paid_amortization, r.currency) }, { key: "sweep", header: "Cash sweep", align: "right", render: (row) => money(row.cash_sweep, r.currency) }, { key: "end", header: "Ending", align: "right", render: (row) => money(row.ending_balance, r.currency) }]} /></Card>}
    <p className="text-2xs text-faint">Entry period {first?.start_date ?? "—"} · Exit period {last?.end_date ?? "—"} · Output hash {model.output_hash}</p>
  </div>;
}

export function UnderwritingWorkbench({ workspaceId, cases }: { workspaceId: string; cases: UnderwritingCaseVersion[] }) {
  const [active, setActive] = useState<CaseKey>(cases.some((x) => x.case_key === "base") ? "base" : cases[0]?.case_key ?? "base");
  const [inputsDirty, setInputsDirty] = useState(false);
  const versionFingerprint = cases.map((item) => `${item.id}:${item.version}:${item.input_hash}`).join("|");
  useEffect(() => setInputsDirty(false), [versionFingerprint]);
  useEffect(() => {
    const markDirty = (event: Event) => {
      const detail = (event as CustomEvent<{ workspaceId?: string }>).detail;
      if (detail?.workspaceId === workspaceId) setInputsDirty(true);
    };
    window.addEventListener("deallens:underwriting-input-dirty", markDirty);
    return () => window.removeEventListener("deallens:underwriting-input-dirty", markDirty);
  }, [workspaceId]);
  const selected = useMemo(() => cases.find((x) => x.case_key === active), [cases, active]);
  if (inputsDirty && cases.length) {
    return <div className="space-y-6"><Card eyebrow="Case architecture" title="Versioned operating and transaction assumptions" subtitle="Build all three cases together to keep periods, capital structure, and calculation policy aligned."><CaseBuilder workspaceId={workspaceId} cases={cases} onModelInputChange={() => setInputsDirty(true)} /></Card><Callout tone="warning" title="Model inputs changed — results hidden">The saved case outputs no longer match the form. Recalculate all three cases to create fresh, hash-addressed versions before reviewing returns or recording a decision.</Callout></div>;
  }
  return <div className="space-y-6"><Card eyebrow="Case architecture" title="Versioned operating and transaction assumptions" subtitle="Build all three cases together to keep periods, capital structure, and calculation policy aligned."><CaseBuilder workspaceId={workspaceId} cases={cases} /></Card>{cases.length ? <><div className="flex gap-1 border-b border-line">{CASES.map((key) => { const model = cases.find((x) => x.case_key === key); return <button key={key} onClick={() => setActive(key)} className={`border-b-2 px-4 py-2 text-xs font-semibold uppercase tracking-wide ${active === key ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"}`}>{key}{model ? ` · v${model.version}` : ""}</button>; })}</div>{selected && <CaseResults workspaceId={workspaceId} model={selected} />}</> : <EmptyPanel title="No underwriting cases" body="Enter the historical baseline, transaction assumptions, debt structure, and operating drivers to create the first governed case set." />}</div>;
}
