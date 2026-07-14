import type {
  Evidence,
  GovernedICPacketCreate,
  UnderwritingCaseVersion,
} from "@/lib/types";

export function approvedPacketCases(cases: UnderwritingCaseVersion[]) {
  return cases.filter((item) => item.latest_decision?.decision === "approved");
}

export function buildGovernedPacketInput({
  title,
  decisionQuestion,
  cases,
  evidence,
  approvedClaimIds = [],
  previousPacketId = null,
  requestedAt = new Date().toISOString(),
}: {
  title: string;
  decisionQuestion: string;
  cases: UnderwritingCaseVersion[];
  evidence: Evidence[];
  approvedClaimIds?: string[];
  previousPacketId?: string | null;
  requestedAt?: string;
}): GovernedICPacketCreate {
  const approved = approvedPacketCases(cases);
  if (!approved.length) throw new Error("Approve at least one underwriting case version before assembling an IC packet.");
  return {
    title: title.trim(),
    assembly_mode: "governed",
    case_version_ids: [...new Set(approved.map((item) => item.id))],
    approved_claim_ids: [...new Set(approvedClaimIds)],
    workspace_evidence_refs: [...new Set(evidence.map((item) => item.ref).filter(Boolean))],
    decision_request: {
      question: decisionQuestion.trim(),
      requested_at: requestedAt,
    },
    previous_packet_id: previousPacketId,
  };
}
