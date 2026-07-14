import { describe, expect, it } from "vitest";
import type { UnderwritingDecision } from "@/lib/types";
import { underwritingDecisionPermissions } from "./underwritingGovernance";

const submitted = { decision: "submitted", actor: "actor-a" } as UnderwritingDecision;

describe("underwriting four-eyes permissions", () => {
  it("blocks the submitter from approving or rejecting the same case", () => {
    const permissions = underwritingDecisionPermissions(submitted, "actor-a");
    expect(permissions.isSubmitter).toBe(true);
    expect(permissions.canReview).toBe(false);
  });

  it("allows a different actor to review a submitted case", () => {
    expect(underwritingDecisionPermissions(submitted, "actor-b").canReview).toBe(true);
  });

  it("requires submission before review and closes finalized versions", () => {
    expect(underwritingDecisionPermissions(null, "actor-a").canSubmit).toBe(true);
    expect(underwritingDecisionPermissions(null, "actor-a").canReview).toBe(false);
    expect(underwritingDecisionPermissions({ decision: "approved", actor: "actor-b" } as UnderwritingDecision, "actor-a").isFinal).toBe(true);
  });
});
