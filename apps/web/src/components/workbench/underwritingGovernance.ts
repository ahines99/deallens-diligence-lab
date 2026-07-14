import type { UnderwritingDecision } from "@/lib/types";

export function underwritingDecisionPermissions(
  latestDecision: UnderwritingDecision | null,
  actorId: string | undefined,
) {
  const isSubmitted = latestDecision?.decision === "submitted";
  const isFinal = latestDecision?.decision === "approved" || latestDecision?.decision === "rejected";
  const isSubmitter = Boolean(isSubmitted && actorId && latestDecision.actor === actorId);
  return {
    canSubmit: Boolean(actorId && !isSubmitted && !isFinal),
    canReview: Boolean(actorId && isSubmitted && !isSubmitter),
    isSubmitter,
    needsSubmission: !isSubmitted && !isFinal,
    isFinal,
  };
}
