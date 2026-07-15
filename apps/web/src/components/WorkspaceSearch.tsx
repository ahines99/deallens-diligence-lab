"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { WorkspaceSearchHit } from "@/lib/types";

const ARTIFACT_ROUTE: Record<string, string> = {
  evidence: "evidence",
  risk: "risks",
  question: "questions",
  memo: "memo",
  filing: "filings",
  document_chunk: "filings",
};

/** Full-text search across a workspace's artifacts (G34). */
export function WorkspaceSearch({ workspaceId, base }: { workspaceId: string; base: string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<WorkspaceSearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    try {
      const res = await api.searchWorkspace(workspaceId, q.trim());
      setHits(res.hits);
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
      setHits([]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-4">
      <form onSubmit={submit}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search this workspace…"
          className="w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none"
          aria-label="Search workspace artifacts"
        />
      </form>
      {hits !== null && (
        <div className="mt-2 rounded-md border border-line-faint bg-panel2 p-2">
          {busy ? (
            <p className="text-2xs text-muted">Searching…</p>
          ) : hits.length === 0 ? (
            <p className="text-2xs text-muted">No matches.</p>
          ) : (
            <ul className="space-y-1">
              {hits.slice(0, 8).map((hit) => (
                <li key={`${hit.artifact_type}-${hit.artifact_id}`}>
                  <Link
                    href={`${base}/${ARTIFACT_ROUTE[hit.artifact_type] ?? ""}`}
                    className="block rounded px-1.5 py-1 hover:bg-panel"
                    onClick={() => setHits(null)}
                  >
                    <span className="text-2xs uppercase tracking-eyebrow text-faint">{hit.artifact_type}</span>
                    <span className="block truncate text-xs text-ink">{hit.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default WorkspaceSearch;
