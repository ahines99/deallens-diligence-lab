import type { MembershipRole, WorkspaceDataClassification } from "@/lib/types";
import type { BadgeTone } from "@/components/ui/Badge";

const CLASSIFICATION: Record<WorkspaceDataClassification, { label: string; tone: BadgeTone; detail: string }> = {
  public: { label: "Public", tone: "green", detail: "Approved public and licensed sources only." },
  internal: { label: "Internal", tone: "indigo", detail: "Firm-internal information; controlled distribution." },
  confidential: { label: "Confidential", tone: "amber", detail: "Deal-sensitive information requiring limited access." },
  restricted: { label: "Restricted", tone: "critical", detail: "Highest-sensitivity information and explicit handling controls." },
};

export function governancePolicyState(
  classification: WorkspaceDataClassification,
  externalLlmAllowed: boolean,
  role: MembershipRole | null,
) {
  const classificationState = CLASSIFICATION[classification];
  const canManage = role === "owner" || role === "admin";
  const exceptional = externalLlmAllowed && (classification === "confidential" || classification === "restricted");
  return {
    ...classificationState,
    canManage,
    externalTone: externalLlmAllowed ? (exceptional ? "critical" as const : "amber" as const) : "green" as const,
    externalLabel: externalLlmAllowed ? "External LLM enabled" : "External LLM blocked",
    externalDetail: externalLlmAllowed
      ? exceptional
        ? "A high-sensitivity workspace has an explicit external-model exception."
        : "Approved external-model processing is permitted for this workspace."
      : "Workspace content may not be sent to an external language model.",
  };
}

export const GOVERNANCE_CLASSIFICATIONS = Object.keys(CLASSIFICATION) as WorkspaceDataClassification[];

export function classificationLabel(classification: WorkspaceDataClassification) {
  return CLASSIFICATION[classification].label;
}
