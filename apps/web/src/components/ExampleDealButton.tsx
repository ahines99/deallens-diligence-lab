"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button, type ButtonVariant } from "@/components/ui/Button";

/**
 * Loads the bundled fictional private deal through the real import/governance pipeline
 * and drops the visitor into its data room.
 */
export function ExampleDealButton({ variant = "secondary" }: { variant?: ButtonVariant }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.loadExampleDeal();
      router.push(`/workspaces/${result.workspace_id}/data-room`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the example deal.");
      setLoading(false);
    }
  }

  return (
    <div className="inline-flex flex-col items-start gap-1">
      <Button onClick={load} variant={variant} disabled={loading}>
        {loading ? (
          <>
            <span
              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
              aria-hidden
            />
            Loading example deal…
          </>
        ) : (
          "Load the example private deal"
        )}
      </Button>
      {error && <span className="max-w-xs text-xs text-negative">{error}</span>}
    </div>
  );
}

export default ExampleDealButton;
