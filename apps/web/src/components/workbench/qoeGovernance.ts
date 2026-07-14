import type { QoEAdjustment } from "@/lib/types";

export function qoeDecisionPermissions(
  adjustment: Pick<QoEAdjustment, "status" | "created_by">,
  actorId: string | null | undefined,
) {
  const isCreator = Boolean(actorId && adjustment.created_by === actorId);
  return {
    isCreator,
    canReview: adjustment.status === "proposed" && Boolean(actorId) && !isCreator,
  };
}
