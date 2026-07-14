"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, InlineError, SelectInput, TextArea, TextInput } from "./Primitives";
import type { QoEAdjustment } from "@/lib/types";
import { useActor } from "@/components/identity/ActorContext";
import { Callout } from "@/components/ui/Callout";
import { qoeDecisionPermissions } from "./qoeGovernance";

export function QoEAdjustmentForm({ workspaceId }: { workspaceId: string }) {
  const { actor } = useActor();
  const router = useRouter(); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); setBusy(true); setError(null);
    try {
      await api.createQoEAdjustment(workspaceId, {
        title: String(data.get("title") || ""), description: String(data.get("description") || ""),
        category: String(data.get("category") || "other"), amount: Number(data.get("amount")), period_end: String(data.get("period_end")),
        bridge_layer: String(data.get("bridge_layer")) as "management" | "sponsor" | "covenant", currency: "USD",
        is_recurring: data.get("is_recurring") === "on", is_run_rate: data.get("is_run_rate") === "on", is_cash: data.get("is_cash") === "on",
        owner: String(data.get("owner") || ""), evidence_ref: String(data.get("evidence_ref") || "") || null,
        source_snapshot_id: String(data.get("source_snapshot_id") || "") || null, source_locator: String(data.get("source_locator") || "") || null, created_by: actor.actorId ?? "unattributed",
      }, actor);
      form.reset(); router.refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Could not add the adjustment."); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Field label="Adjustment title" className="xl:col-span-2"><TextInput name="title" required placeholder="Owner compensation above market" /></Field>
        <Field label="Bridge layer"><SelectInput name="bridge_layer"><option value="management">Management</option><option value="sponsor">Sponsor</option><option value="covenant">Covenant</option></SelectInput></Field>
        <Field label="Period end"><TextInput name="period_end" type="date" required /></Field>
        <Field label="Amount" hint="Positive = add-back"><TextInput name="amount" type="number" step="0.01" required /></Field>
        <Field label="Category"><SelectInput name="category"><option value="owner_compensation">Owner compensation</option><option value="one_time_cost">One-time cost</option><option value="run_rate_synergy">Run-rate synergy</option><option value="accounting_policy">Accounting policy</option><option value="discontinued_operation">Discontinued operation</option><option value="other">Other</option></SelectInput></Field>
        <Field label="Owner"><TextInput name="owner" placeholder="Analyst or workstream" /></Field>
        <Field label="Evidence reference"><TextInput name="evidence_ref" placeholder="E-014" /></Field>
        <Field label="Source snapshot ID"><TextInput name="source_snapshot_id" placeholder="Optional immutable source ID" /></Field>
        <Field label="Source locator"><TextInput name="source_locator" placeholder="QoE.xlsx · Adjustments!B12" /></Field>
        <Field label="Description" className="md:col-span-2"><TextArea name="description" rows={3} placeholder="Basis, calculation, and why the treatment is appropriate" /></Field>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-body">
        <label className="flex items-center gap-2"><input type="checkbox" name="is_cash" defaultChecked className="accent-accent" /> Cash item</label>
        <label className="flex items-center gap-2"><input type="checkbox" name="is_recurring" className="accent-accent" /> Recurring</label>
        <label className="flex items-center gap-2"><input type="checkbox" name="is_run_rate" className="accent-accent" /> Run-rate</label>
        <Button type="submit" disabled={busy}>{busy ? "Adding…" : "Add to review queue"}</Button>
        <InlineError message={error} />
      </div>
    </form>
  );
}

export function QoEDecisionActions({ workspaceId, adjustment }: { workspaceId: string; adjustment: QoEAdjustment }) {
  const { actor, profile } = useActor();
  const router = useRouter(); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const permissions = qoeDecisionPermissions(adjustment, actor.actorId);
  if (adjustment.status !== "proposed") return <span className="text-2xs text-muted">{adjustment.decided_by ? `by ${adjustment.decided_by}` : "Reviewed"}</span>;
  async function decide(decision: "approve" | "reject") {
    setBusy(true); setError(null);
    try { await api.decideQoEAdjustment(workspaceId, adjustment.id, decision, actor.actorId ?? "unattributed", "", actor); router.refresh(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Decision failed."); } finally { setBusy(false); }
  }
  return <div className="max-w-sm"><div className="flex items-center justify-end gap-1"><Button onClick={() => decide("approve")} variant="ghost" disabled={busy||!permissions.canReview}>Approve</Button><Button onClick={() => decide("reject")} variant="ghost" disabled={busy||!permissions.canReview}>Reject</Button>{error && <span className="text-negative" title={error}>!</span>}</div>{permissions.isCreator&&<div className="mt-2"><Callout tone="warning" title="Independent review required">{profile.name} created this adjustment and cannot approve or reject it. Switch identities for four-eyes review.</Callout></div>}</div>;
}
