"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button, type ButtonVariant } from "@/components/ui/Button";

export type GenerateKind = "plan" | "risks" | "questions" | "memo" | "red-team";

const ACTIONS: Record<GenerateKind, { fn: (id: string) => Promise<unknown>; label: string }> = {
  plan: { fn: api.generatePlan, label: "Generate plan" },
  risks: { fn: api.generateRisks, label: "Generate red flags" },
  questions: { fn: api.generateQuestions, label: "Generate questions" },
  memo: { fn: api.generateMemo, label: "Generate IC memo" },
  "red-team": { fn: api.generateRedTeam, label: "Run red-team" },
};

export function GenerateButton({
  kind,
  workspaceId,
  label,
  variant = "primary",
}: {
  kind: GenerateKind;
  workspaceId: string;
  label?: string;
  variant?: ButtonVariant;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const action = ACTIONS[kind];

  async function run() {
    setLoading(true);
    setError(null);
    try {
      await action.fn(workspaceId);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="inline-flex flex-col items-start gap-1">
      <Button onClick={run} variant={variant} disabled={loading}>
        {loading ? (
          <>
            <span
              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
              aria-hidden
            />
            Generating…
          </>
        ) : (
          label ?? action.label
        )}
      </Button>
      {error && <span className="max-w-xs text-xs text-negative">{error}</span>}
    </div>
  );
}

export default GenerateButton;
