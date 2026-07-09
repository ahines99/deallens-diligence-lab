"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { DEAL_TYPE_LABELS } from "@/lib/formatting";
import type { DealType } from "@/lib/types";

const DEAL_TYPES = Object.keys(DEAL_TYPE_LABELS) as DealType[];

const TICKER_CHIPS = ["MSFT", "NVDA", "CRWD", "ORCL", "CRM", "SPSC"];

const inputClass =
  "w-full rounded border border-line-strong bg-panel px-3 py-2 text-sm text-ink shadow-xs placeholder:text-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25";

const labelClass = "mb-1.5 block text-2xs font-semibold uppercase tracking-eyebrow text-muted";

export default function NewWorkspacePage() {
  const router = useRouter();
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [dealType, setDealType] = useState<DealType>("public_equity");
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    const t = ticker.trim().toUpperCase();
    if (!t) {
      setError("Enter a ticker to continue.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const ws = await api.createWorkspace({
        ticker: t,
        deal_type: dealType,
        name: name.trim() || undefined,
        investment_question: question.trim() || undefined,
      });
      router.push(`/workspaces/${ws.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          setError(`Ticker "${t}" not found on SEC EDGAR. Check the symbol and try again.`);
        } else if (err.status === 502) {
          setError("Couldn't reach SEC EDGAR, try again in a moment.");
        } else {
          setError(err.message);
        }
      } else {
        setError("Failed to create workspace.");
      }
      setSubmitting(false);
    }
  }

  const canSubmit = ticker.trim().length > 0 && !submitting;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        eyebrow="New workspace"
        title="Create a diligence workspace"
        subtitle="Enter a public-company ticker. We resolve it on SEC EDGAR, pull its XBRL financials and recent filings, and build a source-grounded diligence pack."
      />

      <Card>
        <form onSubmit={onSubmit} className="space-y-5">
          <div>
            <label htmlFor="ticker" className={labelClass}>
              Ticker <span className="text-severity-high">*</span>
            </label>
            <input
              id="ticker"
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              placeholder="e.g. MSFT"
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              className={`${inputClass} font-mono uppercase tracking-wide`}
              required
            />
            <div className="mt-2 flex flex-wrap gap-2">
              {TICKER_CHIPS.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => {
                    setTicker(t);
                    setError(null);
                  }}
                  className={`rounded-sm border px-2.5 py-1 font-mono text-2xs font-semibold uppercase tracking-wide transition-colors ${
                    ticker === t
                      ? "border-accent bg-accent-soft text-accent"
                      : "border-line bg-panel text-muted hover:border-accent hover:text-accent"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="deal_type" className={labelClass}>
              Deal type
            </label>
            <select
              id="deal_type"
              value={dealType}
              onChange={(e) => setDealType(e.target.value as DealType)}
              className={inputClass}
            >
              {DEAL_TYPES.map((dt) => (
                <option key={dt} value={dt}>
                  {DEAL_TYPE_LABELS[dt]}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="name" className={labelClass}>
              Workspace name{" "}
              <span className="font-normal normal-case tracking-normal text-faint">(optional)</span>
            </label>
            <input
              id="name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Leave blank to auto-fill from the company"
              className={inputClass}
            />
          </div>

          <div>
            <label htmlFor="question" className={labelClass}>
              Investment question{" "}
              <span className="font-normal normal-case tracking-normal text-faint">(optional)</span>
            </label>
            <textarea
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={4}
              placeholder="Leave blank to auto-fill from the company. Anchors the plan, risk screen, and IC memo."
              className={inputClass}
            />
          </div>

          {error && (
            <Callout tone="warning" title="Couldn't create the workspace">
              {error}
            </Callout>
          )}

          <div className="flex items-center gap-3 pt-1">
            <Button type="submit" disabled={!canSubmit}>
              {submitting ? (
                <>
                  <span
                    className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
                    aria-hidden
                  />
                  Ingesting SEC filings…
                </>
              ) : (
                "Create workspace"
              )}
            </Button>
            <Button href="/workspaces" variant="ghost">
              Cancel
            </Button>
          </div>

          {submitting && (
            <p className="text-xs leading-relaxed text-muted">
              Resolving the ticker and pulling real filings + XBRL financials from SEC EDGAR — this
              can take a few seconds.
            </p>
          )}
        </form>
      </Card>

      <Callout tone="info">
        Uses real SEC EDGAR data (XBRL company facts + recent filings). Outputs are AI-assisted drafts
        for human review — not investment advice.
      </Callout>
    </div>
  );
}
