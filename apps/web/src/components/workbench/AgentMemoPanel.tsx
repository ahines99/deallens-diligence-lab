"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import type { AgentGrounding } from "@/lib/types";

/** G59 draft shapes. Canonical home is `@/lib/types` — the orchestrator can lift these there
 * (they are structurally identical to the API contract in `src/schemas/agent_memo.py`). */
export interface AgentMemoSection {
  section: string;
  status: "drafted" | "withheld" | "error";
  answer: string | null;
  grounding: AgentGrounding | null;
  artifact_version_id: string | null;
  decision: "pending" | "accept" | "reject";
  decided_by: string | null;
  decided_at: string | null;
}

export interface AgentMemoDraft {
  workspace_id: string;
  status: "in_review" | "decided" | "not_run";
  reason: string | null;
  sections: AgentMemoSection[];
  generated_at: string | null;
  draft_artifact_id: string | null;
  version: number | null;
  assembled_markdown: string | null;
}

const NOT_RUN_EXPLANATIONS: Record<string, string> = {
  mock: "The deployment runs in deterministic mock mode (LLM_MODE=mock); drafting memo sections needs a live LLM.",
  no_consent:
    "This workspace has not consented to an external LLM (or is classified restricted). Enable consent in workspace governance to draft memo sections.",
  no_api_key: "LLM_MODE=live is set but no LLM_API_KEY is configured.",
};

const DECISION_TONE: Record<AgentMemoSection["decision"], BadgeTone> = {
  pending: "indigo",
  accept: "green",
  reject: "slate",
};

const DECISION_LABEL: Record<AgentMemoSection["decision"], string> = {
  pending: "pending review",
  accept: "accepted",
  reject: "rejected",
};

/** G59 — agent-drafted IC memo sections with per-section grounding and human accept/reject.
 *
 * Each section is drafted by its own sealed agent run and gated independently: a section whose
 * prose contains a number or evidence reference its own tools never produced is WITHHELD (the
 * callout names the exact violations) while sibling sections survive. Only sections a human
 * accepts enter the assembled draft — the agent proposes, the analyst disposes. */
export function AgentMemoPanel({ workspaceId }: { workspaceId: string }) {
  const [draft, setDraft] = useState<AgentMemoDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAgentMemoDraft(workspaceId)
      .then((existing: AgentMemoDraft | null) => {
        if (!cancelled && existing) setDraft(existing);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const runDraft = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setDraft(await api.runAgentMemoDraft(workspaceId));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The memo draft run failed.");
    } finally {
      setBusy(false);
    }
  }, [workspaceId]);

  const decide = useCallback(
    async (section: string, decision: "accept" | "reject") => {
      if (!draft?.draft_artifact_id) return;
      setDeciding(section);
      setError(null);
      try {
        setDraft(
          await api.decideAgentMemoSection(workspaceId, draft.draft_artifact_id, section, decision)
        );
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "Recording the decision failed.");
      } finally {
        setDeciding(null);
      }
    },
    [workspaceId, draft]
  );

  return (
    <div className="space-y-6">
      <Card
        eyebrow="Agent-drafted IC memo"
        title="Draft the memo section by section"
        subtitle="The agent drafts each planned section from governed, read-only tool results. Every section passes the grounding gate on its own: a section containing any number or evidence reference its tools never produced is withheld while the others survive. Only sections you accept enter the assembled draft."
      >
        <div className="space-y-3">
          <Button onClick={runDraft} disabled={busy}>
            {busy ? "Agent drafting…" : "Draft memo sections"}
          </Button>
          {error && (
            <p className="text-xs font-medium text-warn" role="alert">
              {error}
            </p>
          )}
        </div>
      </Card>

      {draft && draft.status === "not_run" && (
        <Callout tone="muted" title="Memo draft did not run">
          {NOT_RUN_EXPLANATIONS[draft.reason ?? ""] ?? `Reason: ${draft.reason}.`}
        </Callout>
      )}

      {draft && draft.status !== "not_run" && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-2xs text-faint">
            <Badge tone={draft.status === "decided" ? "green" : "amber"}>
              {draft.status === "decided" ? "decided" : "in review"}
            </Badge>
            {draft.version !== null && <span>Draft version {draft.version} (append-only)</span>}
            {draft.draft_artifact_id && (
              <span>
                Sealed draft{" "}
                <span className="font-mono">{draft.draft_artifact_id.slice(0, 12)}…</span>
              </span>
            )}
          </div>

          {draft.sections.map((section) => (
            <Card
              key={section.section}
              title={section.section}
              right={
                section.status === "drafted" ? (
                  <Badge tone={DECISION_TONE[section.decision]}>
                    {DECISION_LABEL[section.decision]}
                  </Badge>
                ) : (
                  <Badge tone={section.status === "withheld" ? "red" : "amber"}>
                    {section.status}
                  </Badge>
                )
              }
            >
              {section.status === "drafted" && section.answer && (
                <div className="whitespace-pre-wrap text-sm leading-relaxed text-body">
                  {section.answer}
                </div>
              )}
              {section.status === "withheld" && (
                <Callout tone="warning" title="Withheld by the grounding gate">
                  This section&apos;s draft contained content its own tool results never produced,
                  so no text is served
                  {section.grounding &&
                  (section.grounding.numeric_violations.length > 0 ||
                    section.grounding.unknown_refs.length > 0)
                    ? `: ${[
                        ...section.grounding.numeric_violations,
                        ...section.grounding.unknown_refs,
                      ].join(", ")}`
                    : ""}
                  . The section transcript was still sealed for audit.
                </Callout>
              )}
              {section.status === "error" && (
                <Callout tone="muted" title="Section draft failed">
                  The agent run for this section did not produce an answer (budget exhausted or a
                  provider error). Its transcript, if any, was sealed.
                </Callout>
              )}
              {section.status === "drafted" && section.decision === "pending" && (
                <div className="mt-4 flex gap-2">
                  <Button
                    onClick={() => decide(section.section, "accept")}
                    disabled={deciding !== null}
                  >
                    {deciding === section.section ? "Recording…" : "Accept"}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => decide(section.section, "reject")}
                    disabled={deciding !== null}
                  >
                    Reject
                  </Button>
                </div>
              )}
              {section.decision !== "pending" && section.decided_by && (
                <p className="mt-3 text-2xs text-faint">
                  {DECISION_LABEL[section.decision]} by {section.decided_by}
                </p>
              )}
              {section.artifact_version_id && (
                <p className="mt-3 text-2xs text-faint">
                  Sealed section transcript{" "}
                  <span className="font-mono">{section.artifact_version_id.slice(0, 12)}…</span>
                </p>
              )}
            </Card>
          ))}

          {draft.status === "decided" && (
            <Card
              eyebrow="Assembled from accepted sections only"
              title="Assembled memo draft"
              right={<Badge tone="green">decided</Badge>}
            >
              {draft.assembled_markdown ? (
                <pre className="whitespace-pre-wrap font-serif text-sm leading-relaxed text-body">
                  {draft.assembled_markdown}
                </pre>
              ) : (
                <p className="text-xs text-muted">
                  No sections were accepted, so the assembled draft is empty.
                </p>
              )}
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
