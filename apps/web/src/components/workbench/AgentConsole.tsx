"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import type { AgentRun } from "@/lib/types";

const STATUS_TONE: Record<AgentRun["status"], BadgeTone> = {
  completed: "green",
  rejected_ungrounded: "red",
  budget_exhausted: "amber",
  error: "red",
  not_run: "slate",
};

const NOT_RUN_EXPLANATIONS: Record<string, string> = {
  mock: "The deployment runs in deterministic mock mode (LLM_MODE=mock); the agent needs a live LLM.",
  no_consent:
    "This workspace has not consented to an external LLM (or is classified restricted). Enable consent in workspace governance to run the agent.",
  no_api_key: "LLM_MODE=live is set but no LLM_API_KEY is configured.",
};

/** G57 — run the governed diligence agent and show the full sealed transcript.
 *
 * The verification story is the UI: every tool step is listed, and a rejected answer states the
 * exact ungrounded tokens rather than showing prose the tools never supported. */
export function AgentConsole({ workspaceId }: { workspaceId: string }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const objective = String(data.get("objective") || "").trim();
    if (!objective) return;
    setBusy(true);
    setError(null);
    try {
      setRun(await api.runDiligenceAgent(workspaceId, objective));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The agent run failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card
        eyebrow="Governed tool loop"
        title="Give the agent an objective"
        subtitle="The agent may only call read-only workspace tools (filing search, cited Q&A, risks, evidence, in-memory underwriting scenarios). Its final answer is rejected outright if it contains any number or evidence reference no tool produced."
      >
        <form onSubmit={submit} className="space-y-3">
          <textarea
            name="objective"
            rows={3}
            required
            placeholder="Summarize the top three risks with evidence references, and screen whether a debt-free case clears a 2x MoIC."
            className="w-full rounded border border-line bg-panel px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-accent focus:outline-none"
          />
          <Button type="submit" disabled={busy}>
            {busy ? "Agent working…" : "Run diligence agent"}
          </Button>
          {error && (
            <p className="text-xs font-medium text-warn" role="alert">
              {error}
            </p>
          )}
        </form>
      </Card>

      {run && run.status === "not_run" && (
        <Callout tone="muted" title="Agent did not run">
          {NOT_RUN_EXPLANATIONS[run.reason] ?? `Reason: ${run.reason}.`}
        </Callout>
      )}

      {run && run.status !== "not_run" && (
        <Card
          eyebrow={`Sealed run · ${run.steps_used} tool step${run.steps_used === 1 ? "" : "s"}`}
          title={run.objective}
          right={<Badge tone={STATUS_TONE[run.status]}>{run.status.replaceAll("_", " ")}</Badge>}
        >
          {run.answer && (
            <div className="whitespace-pre-wrap text-sm leading-relaxed text-body">{run.answer}</div>
          )}
          {run.status === "rejected_ungrounded" && run.grounding && (
            <Callout tone="warning" title="Answer withheld — failed the grounding gate">
              The agent&apos;s prose contained content no tool result produced, so it was rejected
              rather than served:{" "}
              {[...run.grounding.numeric_violations, ...run.grounding.unknown_refs].join(", ")}.
              The full transcript below was still sealed for audit.
            </Callout>
          )}
          {run.status === "budget_exhausted" && (
            <Callout tone="warning" title="Step budget exhausted">
              The agent hit its tool-call budget before answering; the partial transcript is
              sealed. Narrow the objective or raise the step budget.
            </Callout>
          )}
          <div className="mt-5 space-y-2">
            <div className="text-2xs font-semibold uppercase tracking-eyebrow text-muted">
              Tool transcript
            </div>
            {run.steps.map((step, index) => (
              <div key={index} className="rounded border border-line bg-panel2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={step.ok ? "indigo" : "red"}>{step.tool}</Badge>
                  <span className="font-mono text-2xs text-faint">
                    {JSON.stringify(step.arguments)}
                  </span>
                </div>
                {step.error && <p className="mt-2 text-xs text-warn">{step.error}</p>}
                {step.result && (
                  <pre className="mt-2 max-h-48 overflow-auto rounded bg-panel p-2 text-2xs text-muted">
                    {JSON.stringify(step.result, null, 2)}
                  </pre>
                )}
              </div>
            ))}
            {!run.steps.length && <p className="text-xs text-muted">No tool calls were made.</p>}
          </div>
          <div className="mt-4 flex flex-wrap gap-3 text-2xs text-faint">
            {run.grounding && run.status === "completed" && (
              <span className="font-semibold text-positive">Grounding gate passed</span>
            )}
            {run.manifest && (
              <span>
                {run.manifest.prompt_id} · {run.manifest.prompt_version} ·{" "}
                <span className="font-mono">{run.manifest.prompt_hash.slice(0, 10)}…</span>
              </span>
            )}
            {run.artifact_version_id && (
              <span>
                Sealed artifact <span className="font-mono">{run.artifact_version_id.slice(0, 12)}…</span>
              </span>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
