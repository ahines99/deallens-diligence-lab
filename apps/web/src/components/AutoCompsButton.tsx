"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";

export function AutoCompsButton({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function onClick() {
    if (loading) return;
    setLoading(true);
    setError(null);
    setNote(null);
    try {
      const added = await api.autoComps(workspaceId);
      setNote(
        added.length > 0
          ? `Added ${added.length} same-SIC peer${added.length === 1 ? "" : "s"}.`
          : "No new same-SIC peers were resolved.",
      );
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 502 ? "Couldn't reach SEC EDGAR. Try again in a moment." : err.message);
      } else {
        setError("Failed to auto-add peers.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={onClick} disabled={loading}>
          {loading ? (
            <>
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
                aria-hidden
              />
              Finding peers…
            </>
          ) : (
            "Auto-add peers (by SIC)"
          )}
        </Button>
        <span className="text-xs text-muted">
          Discovers same-SIC public filers from SEC EDGAR and pulls their XBRL financials.
        </span>
      </div>
      {note && (
        <Callout tone="info" title="Auto-comps">
          {note}
        </Callout>
      )}
      {error && (
        <Callout tone="warning" title="Couldn't auto-add peers">
          {error}
        </Callout>
      )}
    </div>
  );
}

export default AutoCompsButton;
