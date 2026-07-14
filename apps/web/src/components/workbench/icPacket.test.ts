import { describe, expect, it } from "vitest";
import type { Evidence, UnderwritingCaseVersion } from "@/lib/types";
import { buildGovernedPacketInput } from "./icPacket";

describe("governed IC packet payload", () => {
  it("sends immutable source identifiers and no client-owned snapshots", () => {
    const approved = { id: "case-approved", latest_decision: { decision: "approved" } } as UnderwritingCaseVersion;
    const submitted = { id: "case-submitted", latest_decision: { decision: "submitted" } } as UnderwritingCaseVersion;
    const evidence = [{ ref: "EV-001" }, { ref: "EV-001" }, { ref: "EV-002" }] as Evidence[];
    const result = buildGovernedPacketInput({
      title: "IC packet",
      decisionQuestion: "Approve signing authority?",
      cases: [approved, submitted],
      evidence,
      approvedClaimIds: ["claim-approved-1", "claim-approved-1", "claim-approved-2"],
      requestedAt: "2026-07-13T12:00:00Z",
    });

    expect(result).toEqual({
      title: "IC packet",
      assembly_mode: "governed",
      case_version_ids: ["case-approved"],
      approved_claim_ids: ["claim-approved-1", "claim-approved-2"],
      workspace_evidence_refs: ["EV-001", "EV-002"],
      decision_request: { question: "Approve signing authority?", requested_at: "2026-07-13T12:00:00Z" },
      previous_packet_id: null,
    });
    expect(result).not.toHaveProperty("scenario_snapshot");
    expect(result).not.toHaveProperty("model_snapshot");
    expect(result).not.toHaveProperty("evidence_manifest");
  });

  it("blocks governed assembly without an approved case version", () => {
    expect(() => buildGovernedPacketInput({
      title: "IC packet",
      decisionQuestion: "Approve?",
      cases: [],
      evidence: [],
    })).toThrow(/Approve at least one/);
  });
});
