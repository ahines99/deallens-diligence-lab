import type { ICPacket } from "@/lib/types";

export function icDecisionPermissions(
  packet: Pick<ICPacket, "frozen_at" | "submitted_by_actor_id">,
  actorId: string | null | undefined,
) {
  const isSubmitter = Boolean(
    packet.submitted_by_actor_id
    && actorId
    && packet.submitted_by_actor_id === actorId,
  );
  return {
    isSubmitter,
    canDecide: Boolean(packet.frozen_at) && !isSubmitter,
  };
}
