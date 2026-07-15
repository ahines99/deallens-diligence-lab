"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { TextInput } from "@/components/workbench/Primitives";
import type { FilingsQAResult } from "@/lib/types";

const SUGGESTED_QUESTIONS = [
  "What are the most significant risk factors?",
  "How concentrated is revenue among large customers?",
  "What drove revenue growth in the most recent fiscal year?",
  "What competitive pressures does the business face?",
];

export function FilingsQAPanel({ workspaceId }: { workspaceId: string }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<FilingsQAResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.askFilings(workspaceId, text.trim()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The question could not be answered.");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  return (
    <div className="space-y-4">
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
          <TextInput
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about the ingested filings — answers quote the 10-K verbatim"
            aria-label="Question about the filings"
          />
          <Button type="submit" disabled={busy}>
            {busy ? "Searching filings…" : "Ask"}
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-2xs uppercase tracking-eyebrow text-faint">Try</span>
          {SUGGESTED_QUESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => {
                setQuestion(suggestion);
                void ask(suggestion);
              }}
              className="rounded-full border border-line px-2.5 py-1 text-xs text-muted transition-colors hover:border-accent/40 hover:text-accent"
            >
              {suggestion}
            </button>
          ))}
        </div>
      </Card>

      {error && (
        <Callout tone="warning" title="Could not answer">
          {error}
        </Callout>
      )}

      {result && result.status === "abstained" && (
        <Callout tone="info" title="Abstained rather than guessing">
          {result.answer}
          {result.retrieval.abstention_reason && (
            <span className="mt-1 block text-xs text-muted">
              Why: {result.retrieval.abstention_reason}
            </span>
          )}
        </Callout>
      )}

      {result && (result.status === "answered" || result.status === "partial") && (
        <Card
          eyebrow={result.status === "partial" ? "Partial answer" : "Extractive answer"}
          title={result.question}
        >
          {result.status === "partial" && (
            <p className="mb-3 text-xs text-warn">
              This answer covers only part of the question
              {typeof result.retrieval.coverage === "number"
                ? ` (${Math.round(result.retrieval.coverage * 100)}% of its terms)`
                : ""}
              . Treat it as a lead, not a complete answer.
            </p>
          )}
          <p className="text-sm leading-relaxed text-ink">{result.answer}</p>
          <div className="mt-4 space-y-3">
            {result.citations.map((citation, index) => (
              <div key={index} className="rounded-md border border-line-faint bg-panel2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="indigo">{citation.form_type ?? "Filing"}</Badge>
                  <span className="text-xs font-semibold text-ink">{citation.section}</span>
                  {citation.filing_date && (
                    <span className="font-mono text-2xs text-faint">{citation.filing_date}</span>
                  )}
                  {citation.document_url && (
                    <a
                      href={citation.document_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-accent underline"
                    >
                      View on sec.gov
                    </a>
                  )}
                </div>
                <blockquote className="mt-2 border-l-2 border-accent/40 pl-3 text-xs leading-relaxed text-muted">
                  “{citation.quote}”
                </blockquote>
              </div>
            ))}
          </div>
          <p className="mt-4 text-2xs uppercase tracking-eyebrow text-faint">
            Method: deterministic BM25 retrieval, verbatim extraction only — nothing is generated.
            Matched terms: {result.retrieval.matched_terms.join(", ") || "—"}
          </p>
        </Card>
      )}
    </div>
  );
}

export default FilingsQAPanel;
