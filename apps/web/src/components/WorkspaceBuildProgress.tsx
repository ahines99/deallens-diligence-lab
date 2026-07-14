"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import type { WorkspaceBuildStatus, WorkspaceBuildStep } from "@/lib/types";

const POLL_INTERVAL_MS = 2500;

const STEPS: { key: WorkspaceBuildStep; label: string; detail: string }[] = [
  { key: "resolving_company", label: "Resolving company", detail: "Matching the ticker to its SEC CIK and registrant record" },
  { key: "fetching_financials", label: "Fetching XBRL financials", detail: "Company facts, multi-year trends, and forensic inputs" },
  { key: "indexing_filings", label: "Indexing filings", detail: "Recent 10-K, 10-Q, and 8-K filings from EDGAR" },
  { key: "fetching_annual_report", label: "Reading the 10-K", detail: "Downloading the annual report and extracting key sections" },
  { key: "running_analysis", label: "Running analysis", detail: "Risk findings, diligence plan, questions, IC memo, and bear case" },
];

function StepRow({ label, detail, state }: { label: string; detail: string; state: "done" | "active" | "failed" | "pending" }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className={
          state === "done"
            ? "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-positive/15 text-xs text-positive"
            : state === "active"
              ? "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
              : state === "failed"
                ? "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-negative/15 text-xs text-negative"
                : "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line text-xs text-faint"
        }
      >
        {state === "done" ? "✓" : state === "failed" ? "✕" : state === "active" ? (
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        ) : (
          ""
        )}
      </span>
      <span>
        <span
          className={
            state === "pending"
              ? "block text-sm font-semibold text-faint"
              : state === "failed"
                ? "block text-sm font-semibold text-negative"
                : "block text-sm font-semibold text-ink"
          }
        >
          {label}
        </span>
        <span className="block text-xs leading-relaxed text-muted">{detail}</span>
      </span>
    </li>
  );
}

export function WorkspaceBuildProgress({ workspaceId, initial }: { workspaceId: string; initial: WorkspaceBuildStatus }) {
  const router = useRouter();
  const [status, setStatus] = useState<WorkspaceBuildStatus>(initial);
  const [retrying, setRetrying] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);
  const refreshed = useRef(false);

  const poll = useCallback(async () => {
    try {
      const next = await api.getBuildStatus(workspaceId);
      setPollError(null);
      setStatus(next);
      if (next.status === "ready" && !refreshed.current) {
        refreshed.current = true;
        router.refresh();
      }
    } catch (e) {
      // A transient poll failure is not a build failure; keep the last known state.
      setPollError(e instanceof ApiError && e.status !== 0 ? e.message : "Connection lost; retrying…");
    }
  }, [router, workspaceId]);

  useEffect(() => {
    if (status.status !== "building") return;
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [poll, status.status]);

  async function retry() {
    setRetrying(true);
    try {
      const next = await api.retryBuild(workspaceId);
      setStatus(next);
      refreshed.current = false;
    } catch (e) {
      setPollError(e instanceof ApiError ? e.message : "Retry failed. Please try again.");
    } finally {
      setRetrying(false);
    }
  }

  const activeIndex = status.step ? STEPS.findIndex((s) => s.key === status.step) : 0;

  if (status.status === "ready") {
    return (
      <Callout tone="info" title="Workspace ready">
        The build finished. If this page has not updated, refresh it.
      </Callout>
    );
  }

  return (
    <Card
      eyebrow={status.status === "failed" ? "Build failed" : "Building workspace"}
      title={
        status.status === "failed"
          ? `Ingestion of ${status.ticker ?? "the target"} did not complete`
          : `Assembling the diligence pack for ${status.ticker ?? "the target"}`
      }
    >
      <p className="text-sm leading-relaxed text-muted">
        {status.status === "failed"
          ? "The live SEC EDGAR build stopped partway. Nothing fabricated fills the gap — retry to resume from real sources."
          : "Everything below is fetched live from SEC EDGAR and computed deterministically. Larger filers can take a minute."}
      </p>
      <ol className="mt-5 space-y-4">
        {STEPS.map((step, index) => {
          const state =
            status.status === "failed"
              ? index < activeIndex
                ? "done"
                : index === activeIndex
                  ? "failed"
                  : "pending"
              : index < activeIndex
                ? "done"
                : index === activeIndex
                  ? "active"
                  : "pending";
          return <StepRow key={step.key} label={step.label} detail={step.detail} state={state} />;
        })}
      </ol>
      {status.status === "failed" && (
        <div className="mt-5 space-y-3">
          <Callout tone="warning" title="What went wrong">
            {status.error ?? "The data source did not respond."}
          </Callout>
          <Button onClick={retry} disabled={retrying}>
            {retrying ? "Restarting build…" : "Retry build"}
          </Button>
        </div>
      )}
      {pollError && status.status === "building" && (
        <p className="mt-4 text-xs text-muted">{pollError}</p>
      )}
    </Card>
  );
}

export default WorkspaceBuildProgress;
