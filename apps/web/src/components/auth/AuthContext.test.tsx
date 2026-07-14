import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { clearAuthSession, readAuthSession, saveAuthSession } from "@/lib/authSession";
import type { AuthSessionToken } from "@/lib/types";
import { AuthProvider, useAuth } from "./AuthContext";

const bridgeMocks = vi.hoisted(() => ({
  installServerAuthSession: vi.fn(),
  inspectServerAuthSession: vi.fn(),
  clearServerAuthSession: vi.fn(),
}));

vi.mock("@/lib/authBridge", () => bridgeMocks);

function session(organizationId: string, tokenCharacter: string): AuthSessionToken {
  return {
    access_token: `dls_${tokenCharacter.repeat(40)}`,
    token_type: "bearer",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    principal: {
      user_id: "user-1",
      session_id: `session-${tokenCharacter}`,
      email: "jordan@example.test",
      display_name: "Jordan Lee",
      organization_id: organizationId,
      membership_id: `membership-${tokenCharacter}`,
      role: "owner",
    },
    memberships: [
      { id: "member-a", user_id: "user-1", organization_id: "a".repeat(32), role: "owner", status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", email: null, display_name: null },
      { id: "member-b", user_id: "user-1", organization_id: "b".repeat(32), role: "admin", status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", email: null, display_name: null },
    ],
  };
}

function Harness() {
  const auth = useAuth();
  return <div><span>{auth.status}</span><span>{auth.session?.principal.organization_id}</span><button onClick={() => void auth.logout()}>logout</button><button onClick={() => void auth.switchOrganization("b".repeat(32))}>switch</button></div>;
}

describe("AuthProvider session lifecycle", () => {
  beforeEach(() => {
    clearAuthSession();
    bridgeMocks.installServerAuthSession.mockReset().mockResolvedValue(undefined);
    bridgeMocks.inspectServerAuthSession.mockReset().mockResolvedValue({ authenticated: true, expires_at: new Date(Date.now() + 60_000).toISOString() });
    bridgeMocks.clearServerAuthSession.mockReset().mockResolvedValue(undefined);
  });
  afterEach(cleanup);

  it("revokes and removes the browser session on logout", async () => {
    const active = session("a".repeat(32), "a");
    saveAuthSession(active);
    vi.spyOn(api, "currentIdentity").mockResolvedValue({ principal: active.principal, memberships: active.memberships });
    vi.spyOn(api, "listOrganizations").mockResolvedValue([]);
    const logout = vi.spyOn(api, "logout").mockResolvedValue({ revoked: true });

    render(<AuthProvider><Harness /></AuthProvider>);
    expect(await screen.findByText("authenticated")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    expect(await screen.findByText("anonymous")).toBeInTheDocument();
    expect(logout).toHaveBeenCalledOnce();
    expect(bridgeMocks.clearServerAuthSession).toHaveBeenCalledOnce();
    expect(readAuthSession()).toBeNull();
  });

  it("atomically replaces the revoked token when switching organizations", async () => {
    const active = session("a".repeat(32), "a");
    const switched = session("b".repeat(32), "b");
    switched.principal.role = "admin";
    saveAuthSession(active);
    vi.spyOn(api, "currentIdentity").mockResolvedValue({ principal: active.principal, memberships: active.memberships });
    vi.spyOn(api, "listOrganizations").mockResolvedValue([]);
    const switchOrganization = vi.spyOn(api, "switchOrganization").mockResolvedValue(switched);

    render(<AuthProvider><Harness /></AuthProvider>);
    expect(await screen.findByText("a".repeat(32))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "switch" }));

    expect(await screen.findByText("b".repeat(32))).toBeInTheDocument();
    expect(switchOrganization).toHaveBeenCalledWith("b".repeat(32));
    expect(bridgeMocks.installServerAuthSession).toHaveBeenCalledWith(switched);
    expect(readAuthSession()?.principal.organization_id).toBe("b".repeat(32));
    expect(JSON.stringify(readAuthSession())).not.toContain(switched.access_token);
  });
});
