"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ApiError } from "@/lib/api";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import type { AgentComparativeRun } from "@/lib/types";

const STATUS_TONE: Record<string, BadgeTone> = {
  completed: "green",
  rejected_ungrounded: "red",
  budget_exhausted: "amber",
  error: "red",
  not_run: "slate",
};

const NOT_RUN_EXPLANATIONS: Record<string, string> = {
  mock: "The deployment runs in deterministic mock mode (LLM_MODE=mock); comparative agent runs need a live LLM.",
  no_api_key: "LLM_MODE=live is set but no LLM_API_KEY is configured.",
};

const COMP_SLOTS = [1, 2, 3] as const;

/** G63 — one objective across the target plus up to three comp workspaces.
 *
 * Every workspace runs its own harness-scoped G57 agent; the merged answer is a deterministic,
 * provenance-labeled concatenation of the individually grounded answers. Consent is unanimous:
 * a single non-consenting workspace withholds the WHOLE run and is named here — a comparison
 * that silently dropped a workspace would misrepresent the peer set. */
export function AgentComparePanel({ workspaceId }: { workspaceId: string }) {
  const [run, setRun] = useState<AgentComparativeRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const objective = String(data.get("objective") || "").trim();
    const compIds = COMP_SLOTS.map((slot) => String(data.get(`comp-${slot}`) || "").trim()).filter(
      Boolean,
    );
    if (!objective || !compIds.length) {
      setError(compIds.length ? null : "At least one comp workspace ID is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setRun(await api.runComparativeAgent(workspaceId, objective, compIds));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The comparative run failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card
        eyebrow="Comparative run"
        title="One objective, this workspace plus its comps"
        subtitle="Each workspace runs its own governed agent — a tool call can only ever read the workspace it runs in. The merged answer is a deterministic, per-workspace-labeled concatenation of the individually grounded answers; every involved workspace must consent or the whole run is withheld."
      >
        <form onSubmit={submit} className="space-y-3">
          <textarea
            name="objective"
            rows={3}
            required
            placeholder="How concentrated is customer revenue, and how does it compare across the peer set?"
            className="w-full rounded border border-line bg-panel px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-accent focus:outline-none"
          />
          <div className="grid gap-2 sm:grid-cols-3">
            {COMP_SLOTS.map((slot) => (
              <input
                key={slot}
                name={`comp-${slot}`}
                type="text"
                placeholder={`Comp workspace ID ${slot}`}
                className="rounded border border-line bg-panel px-3 py-2 font-mono text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none"
              />
            ))}
          </div>
          <Button type="submit" disabled={busy}>
            {busy ? "Comparing…" : "Run comparative agent"}
          </Button>
          {error && (
            <p className="text-xs font-medium text-warn" role="alert">
              {error}
            </p>
          )}
        </form>
      </Card>

      {run && run.status === "not_run" && (
        <Callout tone="warning" title="Comparative run did not run">
          {run.reason === "no_consent" ? (
            <>
              Workspace{" "}
              <span className="font-mono">{run.blocking_workspace_id ?? "unknown"}</span> has not
              consented to an external LLM (or is classified restricted). Every workspace in a
              comparative run must consent, so the whole run was withheld — no workspace is ever
              silently excluded from a comparison.
            </>
          ) : (
            (run.reason && NOT_RUN_EXPLANATIONS[run.reason]) ?? `Reason: ${run.reason ?? "unknown"}.`
          )}
        </Callout>
      )}

      {run && run.status !== "not_run" && (
        <>
          <Card
            eyebrow={`Per-workspace runs · ${run.per_workspace.length}`}
            title={run.objective}
          >
            <div className="space-y-2">
              {run.per_workspace.map((entry) => (
                <div key={entry.workspace_id} className="rounded border border-line bg-panel2 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={entry.role === "primary" ? "indigo" : "neutral"}>
                      {entry.role}
                    </Badge>
                    <span className="text-sm font-semibold text-ink">{entry.workspace_name}</span>
                    <span className="font-mono text-2xs text-faint">{entry.workspace_id}</span>
                    <Badge tone={STATUS_TONE[entry.status]}>
                      {entry.status.replaceAll("_", " ")}
                    </Badge>
                  </div>
                  {entry.answer && (
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-body">
                      {entry.answer}
                    </p>
                  )}
                  {!entry.answer && (
                    <p className="mt-2 text-xs text-muted">
                      Withheld/failed: {entry.status.replaceAll("_", " ")} ({entry.reason})
                      {entry.grounding && !entry.grounding.grounded && (
                        <>
                          {" "}
                          — ungrounded:{" "}
                          {[
                            ...entry.grounding.numeric_violations,
                            ...entry.grounding.unknown_refs,
                          ].join(", ")}
                        </>
                      )}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap gap-3 text-2xs text-faint">
                    <span>
                      {entry.steps_used} tool step{entry.steps_used === 1 ? "" : "s"}
                      {entry.tools_used.length > 0 && <> · {entry.tools_used.join(", ")}</>}
                    </span>
                    {entry.artifact_version_id && (
                      <span>
                        Sealed run{" "}
                        <span className="font-mono">
                          {entry.artifact_version_id.slice(0, 12)}…
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {run.merged_markdown && (
            <Card eyebrow="Merged answer" title="Per-workspace provenance, deterministic merge">
              <div className="memo-prose text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.merged_markdown}</ReactMarkdown>
              </div>
              <div className="mt-4 flex flex-wrap gap-3 text-2xs text-faint">
                {run.grounding?.grounded && (
                  <span className="font-semibold text-positive">
                    Union grounding gate passed
                  </span>
                )}
                {run.artifact_version_id && (
                  <span>
                    Sealed comparative record{" "}
                    <span className="font-mono">{run.artifact_version_id.slice(0, 12)}…</span>
                  </span>
                )}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
