import { describe, expect, it } from "vitest";
import { qoeDecisionPermissions } from "./qoeGovernance";

describe("QoE four-eyes policy", () => {
  const adjustment = { status: "proposed", created_by: "demo-associate" };

  it("blocks the adjustment creator", () => {
    expect(qoeDecisionPermissions(adjustment, "demo-associate")).toEqual({ isCreator: true, canReview: false });
  });

  it("permits a second demo actor to review", () => {
    expect(qoeDecisionPermissions(adjustment, "demo-principal")).toEqual({ isCreator: false, canReview: true });
  });
});
