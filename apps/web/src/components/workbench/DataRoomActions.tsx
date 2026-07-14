"use client";

import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, InlineError, SelectInput, TextArea, TextInput } from "./Primitives";
import type { FinancialImportException } from "@/lib/types";
import { useActor } from "@/components/identity/ActorContext";

function message(error: unknown) {
  return error instanceof ApiError ? error.message : "The request could not be completed.";
}

export function PrivateTargetForm({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setError(null);
    try {
      await api.createPrivateTarget(workspaceId, {
        name: String(data.get("name") || ""), sector: String(data.get("sector") || ""),
        description: String(data.get("description") || ""), fiscal_year_end: String(data.get("fiscal_year_end") || "") || null,
      });
      router.refresh();
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <Field label="Target company"><TextInput name="name" required placeholder="Company legal name" /></Field>
      <Field label="Sector"><TextInput name="sector" placeholder="Business services" /></Field>
      <Field label="Fiscal year end"><TextInput name="fiscal_year_end" placeholder="December 31" /></Field>
      <Field label="Description" className="sm:col-span-2"><TextArea name="description" rows={3} placeholder="Business model, geography, ownership, and transaction context" /></Field>
      <div className="flex items-center gap-3 sm:col-span-2"><Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create private target"}</Button><InlineError message={error} /></div>
    </form>
  );
}

export function FinancialUpload({ workspaceId }: { workspaceId: string }) {
  const { actor } = useActor();
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) { setError("Choose a CSV or XLSX file."); return; }
    setBusy(true); setError(null); setSummary(null);
    try {
      const sourceName = String(new FormData(event.currentTarget).get("source_name") || "") || file.name;
      const result = file.name.toLowerCase().endsWith(".xlsx")
        ? await api.importFinancialXlsx(workspaceId, file, { sourceName, createdBy: actor.actorId ?? "unattributed" }, actor)
        : await api.importFinancialCsv(workspaceId, file, { sourceName, createdBy: actor.actorId ?? "unattributed" }, actor);
      setSummary(`${result.row_count.toLocaleString()} rows imported · ${result.mapped_count.toLocaleString()} mapped · ${result.open_exception_count.toLocaleString()} exceptions`);
      if (fileRef.current) fileRef.current.value = "";
      router.refresh();
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <Field label="Source label"><TextInput name="source_name" placeholder="FY26 management financials" /></Field>
        <Field label="Financial file" hint="10 MiB max"><TextInput ref={fileRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required className="file:mr-3 file:border-0 file:bg-transparent file:text-xs file:font-semibold file:text-accent" /></Field>
        <Button type="submit" disabled={busy}>{busy ? "Importing…" : "Import financials"}</Button>
      </div>
      <p className="text-2xs leading-relaxed text-faint">CSV and XLSX imports are sealed as immutable source snapshots. Use the import template columns for account, statement, period, value, scale, unit, currency, and source locator.</p>
      {summary && <p className="text-xs font-medium text-positive">{summary}</p>}
      <InlineError message={error} />
    </form>
  );
}

export function AccountMappingForm({ workspaceId }: { workspaceId: string }) {
  const { actor } = useActor();
  const router = useRouter();
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy(true); setError(null);
    try {
      await api.createAccountMapping(workspaceId, {
        raw_account: String(data.get("raw_account") || ""), canonical_account: String(data.get("canonical_account") || ""),
        statement: String(data.get("statement")) as "income_statement" | "balance_sheet" | "cash_flow" | "kpi",
        sign_multiplier: Number(data.get("sign_multiplier") || 1), approved_by: actor.actorId ?? "unattributed", created_by: actor.actorId ?? "unattributed",
      }, actor);
      event.currentTarget.reset(); router.refresh();
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1.2fr_1.2fr_1fr_.6fr_auto] lg:items-end">
      <Field label="Raw account"><TextInput name="raw_account" required placeholder="Sales - Products" /></Field>
      <Field label="Canonical account"><TextInput name="canonical_account" required pattern="[a-z][a-z0-9_]+" placeholder="product_revenue" /></Field>
      <Field label="Statement"><SelectInput name="statement"><option value="income_statement">Income statement</option><option value="balance_sheet">Balance sheet</option><option value="cash_flow">Cash flow</option><option value="kpi">KPI</option></SelectInput></Field>
      <Field label="Sign"><SelectInput name="sign_multiplier"><option value="1">+1</option><option value="-1">−1</option></SelectInput></Field>
      <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Approve mapping"}</Button>
      <div className="sm:col-span-2 lg:col-span-5"><InlineError message={error} /></div>
    </form>
  );
}

export function ExceptionActions({ workspaceId, item }: { workspaceId: string; item: FinancialImportException }) {
  const { actor } = useActor();
  const router = useRouter(); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  async function resolve() {
    setBusy(true); setError(null);
    try { await api.resolveImportException(workspaceId, item.id, actor.actorId ?? "unattributed", actor); router.refresh(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  if (item.state !== "open") return <span className="text-2xs text-positive">Resolved</span>;
  return <div className="flex items-center gap-2"><Button onClick={resolve} variant="ghost" disabled={busy}>{busy ? "Resolving…" : "Resolve"}</Button>{error && <span title={error} className="text-negative">!</span>}</div>;
}
