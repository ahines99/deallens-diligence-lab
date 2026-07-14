"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Field, SelectInput, TextArea, TextInput } from "@/components/workbench/Primitives";
import { ExampleDealButton } from "@/components/ExampleDealButton";
import { DEAL_TYPE_LABELS } from "@/lib/formatting";
import type { DealType, SecSearchResult } from "@/lib/types";

const DEAL_TYPES = Object.keys(DEAL_TYPE_LABELS) as DealType[];
const SEARCH_DEBOUNCE_MS = 250;

// One-click starting points so a first-time visitor never has to know a ticker.
const SUGGESTED_COMPANIES: { ticker: string; name: string }[] = [
  { ticker: "MSFT", name: "Microsoft" },
  { ticker: "NVDA", name: "NVIDIA" },
  { ticker: "CRWD", name: "CrowdStrike" },
  { ticker: "LDOS", name: "Leidos" },
];

function useCompanySearch(query: string, enabled: boolean) {
  const [results, setResults] = useState<SecSearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (!enabled || query.trim().length < 2) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const found = await api.secSearch(query.trim());
        if (!cancelled) setResults(found.slice(0, 8));
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, enabled]);

  return { results, searching };
}

export default function NewWorkspacePage() {
  const router = useRouter();
  const [mode, setMode] = useState<"private" | "public">("public");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SecSearchResult | null>(null);
  const [name, setName] = useState("");
  const [sector, setSector] = useState("");
  const [description, setDescription] = useState("");
  const [dealType, setDealType] = useState<DealType>("public_equity");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const searchBox = useRef<HTMLDivElement>(null);

  const { results, searching } = useCompanySearch(query, mode === "public" && selected === null);

  useEffect(() => {
    function closeOnOutsideClick(event: MouseEvent) {
      if (searchBox.current && !searchBox.current.contains(event.target as Node)) {
        setListOpen(false);
      }
    }
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, []);

  function choose(company: SecSearchResult) {
    setSelected(company);
    setQuery(`${company.name} (${company.ticker})`);
    setListOpen(false);
    setError(null);
  }

  function clearSelection() {
    setSelected(null);
    setQuery("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const ticker = mode === "public" ? (selected?.ticker ?? query.trim().toUpperCase()) : "";
    if (mode === "public" && !ticker) {
      setError("Search for a company or enter its ticker.");
      return;
    }
    if (mode === "private" && !name.trim()) {
      setError("Enter the private target's name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const ws = await api.createWorkspace({
        ticker: mode === "public" ? ticker : undefined,
        name: name.trim() || undefined,
        deal_type: dealType,
        investment_question: question.trim() || undefined,
      });
      if (mode === "private") {
        await api.createPrivateTarget(ws.id, {
          name: name.trim(),
          sector: sector.trim(),
          description: description.trim(),
        });
        router.push(`/workspaces/${ws.id}/data-room`);
      } else {
        // The cockpit shows live build progress while ingestion runs in the background.
        router.push(`/workspaces/${ws.id}`);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create the workspace.");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        eyebrow="New workspace"
        title="Open a deal underwriting workspace"
        subtitle="Connect any public ticker for SEC-grounded research, or start with a private target and management materials. Both paths feed the same governed underwriting workflow."
      />
      <Card>
        <form onSubmit={submit} className="space-y-5">
          <div className="grid grid-cols-2 gap-2 rounded-md bg-panel2 p-1">
            <button
              type="button"
              onClick={() => {
                setMode("public");
                setDealType("public_equity");
                setError(null);
              }}
              className={`rounded px-3 py-2 text-sm font-semibold ${
                mode === "public" ? "bg-panel text-accent shadow-xs" : "text-muted"
              }`}
            >
              Public company
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("private");
                setDealType("buyout");
                setError(null);
              }}
              className={`rounded px-3 py-2 text-sm font-semibold ${
                mode === "private" ? "bg-panel text-accent shadow-xs" : "text-muted"
              }`}
            >
              Private target
            </button>
          </div>

          {mode === "private" && (
            <div className="rounded-md border border-line bg-panel2 p-4">
              <p className="text-sm font-semibold text-ink">No materials handy?</p>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                Load a fully fictional example deal — management financials, data-room documents,
                and proposed QoE adjustments — through the same governed import pipeline, then
                approve and underwrite it yourself. You can also download the{" "}
                <a
                  href="/backend/api/examples/templates/management_financials.csv"
                  className="text-accent underline"
                  download
                >
                  financials CSV template
                </a>{" "}
                to structure your own import.
              </p>
              <div className="mt-3">
                <ExampleDealButton />
              </div>
            </div>
          )}

          {mode === "public" && (
            <div ref={searchBox} className="relative">
              <Field label="Company">
                <TextInput
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setSelected(null);
                    setListOpen(true);
                  }}
                  onFocus={() => setListOpen(true)}
                  required
                  placeholder="Search by company name or ticker — e.g. Microsoft or MSFT"
                  autoComplete="off"
                  aria-expanded={listOpen && results.length > 0}
                  aria-autocomplete="list"
                />
              </Field>
              {listOpen && selected === null && query.trim().length >= 2 && (
                <ul className="absolute z-10 mt-1 w-full overflow-hidden rounded-md border border-line bg-panel shadow-sm">
                  {results.map((company) => (
                    <li key={company.cik}>
                      <button
                        type="button"
                        onClick={() => choose(company)}
                        className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-panel2"
                      >
                        <span className="truncate text-ink">{company.name}</span>
                        <span className="shrink-0 font-mono text-xs text-muted">{company.ticker}</span>
                      </button>
                    </li>
                  ))}
                  {results.length === 0 && (
                    <li className="px-3 py-2 text-sm text-muted">
                      {searching ? "Searching SEC registrants…" : "No SEC registrant matches that search."}
                    </li>
                  )}
                </ul>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="text-2xs uppercase tracking-eyebrow text-faint">Try</span>
                {SUGGESTED_COMPANIES.map((company) => (
                  <button
                    key={company.ticker}
                    type="button"
                    onClick={() => choose({ ...company, cik: company.ticker })}
                    className="rounded-full border border-line px-2.5 py-1 text-xs text-muted transition-colors hover:border-accent/40 hover:text-accent"
                  >
                    {company.name}
                  </button>
                ))}
                {selected && (
                  <button type="button" onClick={clearSelection} className="text-xs text-muted underline">
                    Clear
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={mode === "private" ? "Target / project name" : "Workspace name"}>
              <TextInput
                value={name}
                onChange={(e) => setName(e.target.value)}
                required={mode === "private"}
                placeholder={mode === "private" ? "Project Atlas / Target Company" : "Optional; defaults from SEC"}
              />
            </Field>
            <Field label="Deal type">
              <SelectInput value={dealType} onChange={(e) => setDealType(e.target.value as DealType)}>
                {DEAL_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {DEAL_TYPE_LABELS[value]}
                  </option>
                ))}
              </SelectInput>
            </Field>
              {mode === "private" && (
              <>
                <Field label="Sector">
                  <TextInput
                    value={sector}
                    onChange={(e) => setSector(e.target.value)}
                    placeholder="Business services"
                  />
                </Field>
                <Field label="Target description" className="sm:col-span-2">
                  <TextArea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={3}
                    placeholder="Business model, ownership, geography, and transaction context"
                  />
                </Field>
              </>
            )}
            <Field label="Investment question" className="sm:col-span-2">
              <TextArea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={4}
                placeholder="What must be true for this investment to produce the target return with acceptable downside protection?"
              />
            </Field>
          </div>

          {error && (
            <Callout tone="warning" title="Could not create workspace">
              {error}
            </Callout>
          )}

          <div className="flex gap-3">
            <Button type="submit" disabled={busy}>
              {busy
                ? mode === "public"
                  ? "Starting live SEC build…"
                  : "Creating deal room…"
                : "Create workspace"}
            </Button>
            <Button href="/workspaces" variant="ghost">
              Cancel
            </Button>
          </div>
        </form>
      </Card>
      <Callout tone="info">
        Private inputs remain labeled as user-provided and every imported source is versioned. Public targets
        use SEC EDGAR. Outputs require human review and are not investment advice.
      </Callout>
    </div>
  );
}
