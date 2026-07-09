"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";

const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/40";

export function GovConFetchForm({
  workspaceId,
  defaultName,
  label = "Fetch federal awards",
}: {
  workspaceId: string;
  defaultName?: string;
  label?: string;
}) {
  const router = useRouter();
  const [name, setName] = useState(defaultName ?? "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const trimmed = name.trim();
      await api.generateGovCon(workspaceId, trimmed || undefined);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 502) {
          setError("Couldn't reach USAspending.gov. Try again in a moment.");
        } else if (err.status === 404) {
          setError("Workspace not found. Refresh and try again.");
        } else {
          setError(err.message);
        }
      } else {
        setError("Failed to fetch federal awards.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="flex-1">
          <label htmlFor="govcon-recipient" className="sr-only">
            Recipient name
          </label>
          <input
            id="govcon-recipient"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="defaults to the target company"
            autoCorrect="off"
            spellCheck={false}
            className={inputClass}
          />
        </div>
        <Button type="submit" disabled={loading}>
          {loading ? (
            <>
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
                aria-hidden
              />
              Fetching…
            </>
          ) : (
            label
          )}
        </Button>
      </div>
      <p className="text-xs text-slate-500">
        Queries USAspending.gov for federal contract awards and re-runs the GovCon analysis — this
        takes a few seconds.
      </p>
      {error && (
        <Callout tone="warning" title="Couldn't fetch federal awards">
          {error}
        </Callout>
      )}
    </form>
  );
}

export default GovConFetchForm;
