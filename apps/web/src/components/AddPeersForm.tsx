"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";

const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/40";

export function AddPeersForm({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const [raw, setRaw] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function parseTickers(value: string): string[] {
    return Array.from(
      new Set(
        value
          .split(/[\s,]+/)
          .map((t) => t.trim().toUpperCase())
          .filter((t) => t.length > 0),
      ),
    );
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (loading) return;
    const tickers = parseTickers(raw);
    if (tickers.length === 0) {
      setError("Enter one or more peer tickers (comma or space separated).");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.addComps(workspaceId, tickers);
      setRaw("");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          setError("One or more tickers were not found on SEC EDGAR. Check the symbols and try again.");
        } else if (err.status === 502) {
          setError("Couldn't reach SEC EDGAR, try again in a moment.");
        } else {
          setError(err.message);
        }
      } else {
        setError("Failed to add peers.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="flex-1">
          <label htmlFor="peers" className="sr-only">
            Peer tickers
          </label>
          <input
            id="peers"
            type="text"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            placeholder="Add peer tickers, e.g. NVDA CRM ORCL"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            className={`${inputClass} font-mono uppercase tracking-wide`}
          />
        </div>
        <Button type="submit" disabled={loading}>
          {loading ? (
            <>
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
                aria-hidden
              />
              Adding peers…
            </>
          ) : (
            "Add peers"
          )}
        </Button>
      </div>
      <p className="text-xs text-slate-500">
        Real public-company tickers, comma or space separated. Financials are pulled from SEC XBRL and
        the benchmark re-runs automatically.
      </p>
      {error && (
        <Callout tone="warning" title="Couldn't add peers">
          {error}
        </Callout>
      )}
    </form>
  );
}

export default AddPeersForm;
