"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, API_BASE } from "@/lib/api";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import type { AgentRun, AgentStep } from "@/lib/types";

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

/** How the console hunts for its own sealed transcript after an ambiguous failure: the run may
 * still be executing server-side, so it polls the runs list for a record carrying THIS
 * submission's client_request_id. Bounded — an unfinished run is reported honestly. */
const RECOVERY_ATTEMPTS = 6;
const RECOVERY_DELAY_MS = 2_000;

/** Streaming (G61) failure taxonomy — which recovery is safe depends on WHEN the stream died. */
/** The request failed before a response opened. The server MAY still have received it, so the
 * only safe retry is one the server can deduplicate (same client_request_id). */
class StreamUnavailable extends Error {}
/** The response opened (the server-side run started), then the connection died before a
 * terminal frame. Recovery must NEVER re-run the agent — it reloads the sealed transcript. */
class StreamDropped extends Error {}

type StreamFrame = { event: string; data: Record<string, unknown> };

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Minimal SSE frame parser: `event: <type>\ndata: <json>\n\n`. EventSource cannot POST, so the
 * console reads the fetch response body directly. */
function parseFrameBlock(block: string): StreamFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> };
  } catch {
    return null;
  }
}

/** A frame's `step` payload is server-authored, but render safety must not rest on that: a
 * JSON-valid frame without a well-formed step is dropped rather than crashing StepCard. */
function asAgentStep(value: unknown): AgentStep | null {
  if (!value || typeof value !== "object") return null;
  const step = value as Partial<AgentStep>;
  return typeof step.tool === "string" && typeof step.ok === "boolean"
    ? (step as AgentStep)
    : null;
}

/** POST to the SSE run endpoint (via the same-origin `/backend` proxy) and stream frames until
 * the terminal `finished` frame, which carries the full sealed run record. */
async function streamAgentRun(
  workspaceId: string,
  objective: string,
  requestId: string,
  onStep: (step: AgentStep) => void,
  signal: AbortSignal,
): Promise<AgentRun> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/workspaces/${workspaceId}/agent/run-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ objective, max_steps: 8, client_request_id: requestId }),
      signal,
    });
  } catch {
    // No response opened — but the request MAY have reached the server (e.g. a reset after the
    // body was sent). Only a request-id-deduplicated retry is safe from here.
    throw new StreamUnavailable();
  }
  if (!response.ok) {
    let detail = `The agent stream request failed (${response.status}).`;
    try {
      const parsed = (await response.json()) as { detail?: unknown };
      if (parsed?.detail) detail = String(parsed.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, detail);
  }
  // From here the server has started the run; any failure below is a mid-run drop.
  if (!response.body) throw new StreamDropped();
  const reader = response.body.getReader();
  try {
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const frame = parseFrameBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (!frame) continue;
        if (frame.event === "tool_step") {
          const step = asAgentStep(frame.data.step);
          if (step) onStep(step);
        } else if (frame.event === "finished") {
          return frame.data as unknown as AgentRun;
        } else if (frame.event === "error") {
          const detail = String(frame.data.detail ?? "The agent run failed.");
          // stream_timeout means the stream outwaited the worker, not that the run failed —
          // the worker keeps running and seals, so recover like any mid-run drop.
          if (detail === "stream_timeout") throw new StreamDropped();
          throw new ApiError(0, detail);
        }
      }
    }
  } catch (caught) {
    if (caught instanceof ApiError || caught instanceof StreamDropped) throw caught;
    throw new StreamDropped();
  } finally {
    // Releases the connection on every exit — including component unmount aborting mid-read.
    reader.cancel().catch(() => undefined);
  }
  // The connection closed without a terminal frame — the sealed artifact is the replay source.
  throw new StreamDropped();
}

type RecoveryResult = { run: AgentRun | null; error: ApiError | null };

/** Poll the sealed-transcript list for THIS submission's run (matched by client_request_id —
 * never `runs[0]`, which races the end-of-run seal and can be a previous run entirely). The
 * poll stops on abort (unmount or a newer submission) and reports a persistent listing
 * failure instead of pretending the run may still be executing. */
async function recoverSealedRun(
  workspaceId: string,
  requestId: string,
  signal: AbortSignal,
): Promise<RecoveryResult> {
  let lastError: ApiError | null = null;
  for (let attempt = 0; attempt < RECOVERY_ATTEMPTS; attempt += 1) {
    if (signal.aborted) return { run: null, error: null };
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, RECOVERY_DELAY_MS));
      if (signal.aborted) return { run: null, error: null };
    }
    try {
      const runs = await api.listAgentRuns(workspaceId);
      const match = runs.find((candidate) => candidate.client_request_id === requestId);
      if (match) return { run: match, error: null };
      lastError = null;
    } catch (caught) {
      // Transient listing failures keep polling; an auth-shaped failure will not heal by
      // waiting, so it surfaces instead of the misleading still-running message.
      if (caught instanceof ApiError) {
        lastError = caught;
        if (caught.status === 401 || caught.status === 403) break;
      }
    }
  }
  return { run: null, error: lastError };
}

const STILL_RUNNING_MESSAGE =
  "The connection was lost and the run's sealed transcript has not appeared yet — it may still " +
  "be executing server-side. It will show up in the runs list when it finishes; it was not " +
  "re-run.";

function StepCard({ step }: { step: AgentStep }) {
  return (
    <div className="rounded border border-line bg-panel2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={step.ok ? "indigo" : "red"}>{step.tool}</Badge>
        <span className="font-mono text-2xs text-faint">{JSON.stringify(step.arguments)}</span>
      </div>
      {step.error && <p className="mt-2 text-xs text-warn">{step.error}</p>}
      {step.result && (
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-panel p-2 text-2xs text-muted">
          {JSON.stringify(step.result, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** G57/G61 — run the governed diligence agent, streaming the tool timeline live over SSE, and
 * show the full sealed transcript.
 *
 * The verification story is the UI: every tool step is listed as it happens, and a rejected
 * answer states the exact ungrounded tokens rather than showing prose the tools never supported.
 * A dropped stream is never resumed and the agent is never re-run: each submission carries a
 * client_request_id the server deduplicates, and every recovery path reloads the sealed
 * transcript matched to that id — the sealed artifact stays the source of truth. */
export function AgentConsole({ workspaceId }: { workspaceId: string }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      // Unmounting mid-stream releases the reader/connection; the server worker still completes
      // and seals by design, and no state is written into the unmounted component.
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  /** Recover THIS submission's sealed record and show it with `notice`; otherwise surface the
   * listing failure or the honest still-running message. Shared by every recovery path. */
  async function recoverAndShow(
    controller: AbortController,
    requestId: string,
    notice: string,
  ): Promise<void> {
    const recovery = await recoverSealedRun(workspaceId, requestId, controller.signal);
    if (!mountedRef.current || controller.signal.aborted) return;
    if (recovery.run) {
      setRun(recovery.run);
      setNotice(notice);
    } else if (recovery.error) {
      setError(recovery.error.message);
    } else {
      setError(STILL_RUNNING_MESSAGE);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const objective = String(data.get("objective") || "").trim();
    if (!objective) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = newRequestId();
    setBusy(true);
    setError(null);
    setNotice(null);
    setRun(null);
    setLiveSteps([]);
    try {
      const streamed = await streamAgentRun(
        workspaceId,
        objective,
        requestId,
        (step) => setLiveSteps((previous) => [...previous, step]),
        controller.signal,
      );
      if (mountedRef.current && !controller.signal.aborted) setRun(streamed);
    } catch (caught) {
      if (!mountedRef.current || controller.signal.aborted) return;
      if (caught instanceof StreamDropped) {
        // Mid-run drop: the run already executed (or is executing) server-side. Reload the
        // sealed transcript matched to THIS submission's request id — never re-run.
        await recoverAndShow(
          controller,
          requestId,
          "The live stream dropped mid-run; showing the sealed run record reloaded from the transcript.",
        );
      } else if (caught instanceof StreamUnavailable) {
        // The stream request opened no response, so the run's fate is unknown. The fallback
        // POST carries the SAME request id: the server refuses a still-running duplicate (409)
        // and replays an already-sealed one, so this can never double-run the agent.
        try {
          const fallback = await api.runDiligenceAgent(workspaceId, objective, 8, requestId);
          if (!mountedRef.current || controller.signal.aborted) return;
          setRun(fallback);
          setNotice("Live streaming is unavailable; the run completed without streaming.");
        } catch (fallbackFailure) {
          if (!mountedRef.current || controller.signal.aborted) return;
          if (fallbackFailure instanceof ApiError && fallbackFailure.status === 409) {
            // The first POST did reach the server and its run is still executing — recover the
            // sealed transcript instead of surfacing the duplicate refusal as a failure.
            await recoverAndShow(
              controller,
              requestId,
              "The stream request was interrupted but the run had already started; showing its sealed run record.",
            );
          } else {
            setError(
              fallbackFailure instanceof ApiError
                ? fallbackFailure.message
                : "The agent run failed.",
            );
          }
        }
      } else {
        setError(caught instanceof ApiError ? caught.message : "The agent run failed.");
      }
    } finally {
      // Only the CURRENT submission may clear the busy UI: a stale submission's late recovery
      // must not wipe a newer run's spinner or live timeline.
      if (mountedRef.current && abortRef.current === controller) {
        setBusy(false);
        setLiveSteps([]);
      }
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

      {notice && (
        <Callout tone="muted" title="Streaming note">
          {notice}
        </Callout>
      )}

      {busy && liveSteps.length > 0 && (
        <Card
          eyebrow="Live run"
          title="Tool timeline (streaming)"
          subtitle="Steps arrive as the agent works; the sealed run record replaces this timeline when the run finishes."
        >
          <div className="space-y-2" data-testid="live-timeline">
            {liveSteps.map((step, index) => (
              <StepCard key={index} step={step} />
            ))}
          </div>
        </Card>
      )}

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
              <StepCard key={index} step={step} />
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
