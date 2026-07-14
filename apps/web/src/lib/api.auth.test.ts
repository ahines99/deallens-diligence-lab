import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { clearAuthSession, readAuthSession, saveAuthSession } from "./authSession";
import {
  authorizationFromSessionCookie,
  encodeSessionCookie,
} from "./sessionCookie";
import type { AuthSessionToken } from "./types";

function session(token = `dls_${"a".repeat(40)}`): AuthSessionToken {
  return {
    access_token: token,
    token_type: "bearer",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    principal: {
      user_id: "user-1",
      session_id: "session-1",
      email: "alex@example.test",
      display_name: "Alex Morgan",
      organization_id: "1".repeat(32),
      membership_id: "membership-1",
      role: "owner",
    },
    memberships: [],
  };
}

describe("authenticated API requests", () => {
  beforeEach(() => {
    clearAuthSession();
    vi.unstubAllGlobals();
  });

  it("derives the upstream Authorization header from the secure opaque-session cookie", () => {
    const active = session();
    const cookie = encodeSessionCookie(active.access_token, active.expires_at);
    expect(authorizationFromSessionCookie(cookie)).toBe(`Bearer ${active.access_token}`);
    expect(authorizationFromSessionCookie(`${Date.now() - 1}.${active.access_token}`)).toBeNull();
  });

  it("never stores the bearer in JavaScript storage and clears rejected identity metadata", async () => {
    const active = session();
    saveAuthSession(active);
    expect(JSON.stringify(readAuthSession())).not.toContain(active.access_token);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(active), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Expired" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.login({ email: "alex@example.test", password: "correct horse portfolio battery" });
    expect(new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers).has("Authorization")).toBe(false);

    await expect(api.listWorkspaces()).rejects.toMatchObject({ status: 401 });
    expect(readAuthSession()).toBeNull();
  });
});
