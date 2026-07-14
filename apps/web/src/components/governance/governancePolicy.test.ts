import { describe, expect, it } from "vitest";
import { governancePolicyState } from "./governancePolicy";

describe("workspace governance policy states", () => {
  it("permits owners and admins to manage policy", () => {
    expect(governancePolicyState("internal", false, "owner").canManage).toBe(true);
    expect(governancePolicyState("internal", false, "admin").canManage).toBe(true);
  });

  it("keeps policy read-only for members, viewers, and demo sessions", () => {
    expect(governancePolicyState("internal", false, "member").canManage).toBe(false);
    expect(governancePolicyState("internal", false, "viewer").canManage).toBe(false);
    expect(governancePolicyState("internal", false, null).canManage).toBe(false);
  });

  it("raises the visible policy state for high-sensitivity external-model exceptions", () => {
    const state = governancePolicyState("restricted", true, "owner");
    expect(state.tone).toBe("critical");
    expect(state.externalTone).toBe("critical");
    expect(state.externalDetail).toContain("high-sensitivity");
  });

  it("represents blocked external processing as the controlled state", () => {
    const state = governancePolicyState("confidential", false, "viewer");
    expect(state.externalTone).toBe("green");
    expect(state.externalLabel).toBe("External LLM blocked");
  });
});
