"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth/AuthContext";
import { Badge } from "@/components/ui/Badge";
import { api, ApiError } from "@/lib/api";
import type { Workspace, WorkspaceDataClassification } from "@/lib/types";
import {
  classificationLabel,
  GOVERNANCE_CLASSIFICATIONS,
  governancePolicyState,
} from "./governancePolicy";

export function WorkspaceGovernanceControl({ workspaceId, initialWorkspace }: { workspaceId: string; initialWorkspace?: Workspace | null }) {
  const auth = useAuth();
  const [workspace, setWorkspace] = useState<Workspace | null>(initialWorkspace ?? null);
  const [classification, setClassification] = useState<WorkspaceDataClassification>(initialWorkspace?.data_classification ?? "internal");
  const [externalAllowed, setExternalAllowed] = useState(initialWorkspace?.external_llm_allowed ?? false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (auth.status !== "authenticated") return;
    let active = true;
    setLoading(true);
    setError(null);
    api.getWorkspace(workspaceId)
      .then((overview) => {
        if (!active) return;
        setWorkspace(overview.workspace);
        setClassification(overview.workspace.data_classification);
        setExternalAllowed(overview.workspace.external_llm_allowed);
      })
      .catch((caught) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Could not load governance policy.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [auth.session?.principal.organization_id, auth.status, workspaceId]);

  const role = auth.session?.principal.role ?? null;
  const state = governancePolicyState(classification, externalAllowed, role);
  const sameOrganization = Boolean(
    workspace?.organization_id
      && auth.session?.principal.organization_id === workspace.organization_id,
  );
  const canManage = state.canManage && sameOrganization;
  const dirty = useMemo(() => Boolean(
    workspace
      && (classification !== workspace.data_classification || externalAllowed !== workspace.external_llm_allowed),
  ), [classification, externalAllowed, workspace]);

  async function save() {
    if (!canManage || !workspace) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await api.updateWorkspaceGovernance(workspaceId, {
        data_classification: classification,
        external_llm_allowed: externalAllowed,
      });
      setWorkspace(updated);
      setClassification(updated.data_classification);
      setExternalAllowed(updated.external_llm_allowed);
      setSaved(true);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not update governance policy.");
    } finally {
      setSaving(false);
    }
  }

  if (!workspace && auth.status === "anonymous") return null;
  if (!workspace) {
    return (
      <div className="mb-5 rounded-md border border-line bg-panel px-3.5 py-3 shadow-panel">
        <span className="text-2xs font-semibold uppercase tracking-eyebrow text-muted">Governance</span>
        <p className="mt-2 text-2xs leading-relaxed text-muted">{loading || !error ? "Loading workspace policy…" : error}</p>
      </div>
    );
  }

  return (
    <details className="mb-5 rounded-md border border-line bg-panel shadow-panel">
      <summary className="cursor-pointer list-none px-3.5 py-3 [&::-webkit-details-marker]:hidden">
        <div className="flex items-center justify-between gap-2">
          <span className="text-2xs font-semibold uppercase tracking-eyebrow text-muted">Governance</span>
          {loading ? <span className="h-4 w-16 animate-pulse rounded bg-sunken" /> : <Badge tone={state.tone}>{state.label}</Badge>}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2"><Badge tone={state.externalTone}>{state.externalLabel}</Badge><span className="text-2xs text-faint" aria-hidden>⌄</span></div>
      </summary>
      <div className="border-t border-line px-3.5 py-3">
        <>
            <label className="block">
              <span className="mb-1.5 block text-[10px] font-semibold uppercase tracking-wide text-muted">Data classification</span>
              <select
                value={classification}
                disabled={!canManage || saving}
                onChange={(event) => { setClassification(event.target.value as WorkspaceDataClassification); setSaved(false); }}
                className="w-full rounded border border-line-strong bg-panel px-2 py-1.5 text-xs text-ink focus:border-accent focus:outline-none disabled:bg-panel2 disabled:text-muted"
              >
                {GOVERNANCE_CLASSIFICATIONS.map((item) => <option key={item} value={item}>{classificationLabel(item)}</option>)}
              </select>
            </label>
            <p className="mt-1.5 text-[10px] leading-relaxed text-faint">{state.detail}</p>
            <label className={`mt-3 flex items-start gap-2 rounded border p-2.5 ${externalAllowed ? "border-[#e6d6b6] bg-[#fbf7ef]" : "border-line bg-panel2"}`}>
              <input type="checkbox" checked={externalAllowed} disabled={!canManage || saving} onChange={(event) => { setExternalAllowed(event.target.checked); setSaved(false); }} className="mt-0.5 h-3.5 w-3.5 accent-accent" />
              <span><span className="block text-xs font-semibold text-ink">Allow external LLM processing</span><span className="mt-0.5 block text-[10px] leading-relaxed text-muted">{state.externalDetail}</span></span>
            </label>
            {auth.status === "anonymous" && <p className="mt-3 rounded bg-panel2 p-2 text-[10px] leading-relaxed text-muted">Sign in as an organization owner or admin to change this policy.</p>}
            {auth.status === "authenticated" && !canManage && <p className="mt-3 rounded bg-panel2 p-2 text-[10px] leading-relaxed text-muted">Policy is read-only for the current {role ?? "user"} membership.</p>}
            {canManage && <button type="button" onClick={() => void save()} disabled={!dirty || saving} className="mt-3 w-full rounded bg-accent px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-45">{saving ? "Saving policy…" : "Save governance policy"}</button>}
            {saved && <p role="status" className="mt-2 text-[10px] font-semibold text-positive">Governance policy saved.</p>}
        </>
        {error && <p role="alert" className="mt-2 text-[10px] leading-relaxed text-negative">{error}</p>}
      </div>
    </details>
  );
}
