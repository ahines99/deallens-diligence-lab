import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("underwriting actor attribution", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the selected actor header for QoE creation and independent review", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const creator = { actorId: "demo-associate", actorName: "Alex Morgan", roles: ["associate"] };
    const reviewer = { actorId: "demo-principal", actorName: "Jordan Lee", roles: ["principal"] };

    await api.createQoEAdjustment("workspace-1", {
      bridge_layer: "sponsor", title: "Normalized rent", amount: 25,
      period_end: "2026-06-30", created_by: creator.actorId,
    }, creator);
    await api.decideQoEAdjustment("workspace-1", "adjustment-1", "approve", reviewer.actorId, "", reviewer);

    const createHeaders = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    const decisionHeaders = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers);
    expect(createHeaders.get("X-Actor-ID")).toBe("demo-associate");
    expect(decisionHeaders.get("X-Actor-ID")).toBe("demo-principal");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({ decided_by: "demo-principal" });
  });
});
