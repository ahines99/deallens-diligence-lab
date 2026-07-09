import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { CLAIM_TYPE_LABELS } from "@/lib/formatting";
import type { ClaimType } from "@/lib/types";

const CLAIM_TONE: Record<ClaimType, BadgeTone> = {
  fact: "green",
  calculation: "indigo",
  inference: "amber",
  assumption: "slate",
};

export function ClaimBadge({ type }: { type: ClaimType }) {
  return <Badge tone={CLAIM_TONE[type] ?? "neutral"}>{CLAIM_TYPE_LABELS[type] ?? type}</Badge>;
}

export default ClaimBadge;
