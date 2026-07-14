import { describe, expect, it } from "vitest";
import { icDecisionPermissions } from "./icDecisionGovernance";

describe("IC decision four-eyes policy", () => {
  const packet = { frozen_at: "2026-07-13T12:00:00Z", submitted_by_actor_id: "submitter-b" };

  it("blocks the submitting actor even when a different actor created the packet", () => {
    expect(icDecisionPermissions(packet, "submitter-b")).toEqual({ isSubmitter: true, canDecide: false });
  });

  it("permits an independent actor, including the original creator", () => {
    expect(icDecisionPermissions(packet, "creator-a")).toEqual({ isSubmitter: false, canDecide: true });
  });

  it("requires the packet to be frozen", () => {
    expect(icDecisionPermissions({ ...packet, frozen_at: null }, "partner-c").canDecide).toBe(false);
  });
});
