import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as proxyPost } from "@/app/backend/[...path]/route";
import { POST as bridgePost } from "@/app/auth/session/route";
import {
  BodyLimitError,
  DEFAULT_PROXY_BODY_LIMIT,
  LARGE_UPLOAD_BODY_LIMIT,
  readBodyWithLimit,
  resolveProxyPolicy,
} from "./backendProxyPolicy";
import { AUTH_COOKIE_NAME, encodeSessionCookie } from "./sessionCookie";

function request(path: string, init: ConstructorParameters<typeof NextRequest>[1] = {}) {
  return new NextRequest(`http://localhost${path}`, init);
}

describe("backend proxy policy", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("scopes the relay to normalized API paths and grants 20 MiB only to upload endpoints", () => {
    expect(resolveProxyPolicy(["metrics"])).toBeNull();
    expect(resolveProxyPolicy(["api", "..", "metrics"])).toBeNull();
    expect(resolveProxyPolicy(["api", "deals\\escape"])).toBeNull();
    expect(resolveProxyPolicy(["api", "workspaces"])).toEqual({
      upstreamPath: "api/workspaces",
      bodyLimit: DEFAULT_PROXY_BODY_LIMIT,
    });
    expect(resolveProxyPolicy([
      "api", "workspaces", "workspace-1", "underwriting", "financial-imports", "xlsx",
    ])?.bodyLimit).toBe(LARGE_UPLOAD_BODY_LIMIT);
    expect(resolveProxyPolicy([
      "api", "deals", "deal-1", "intelligence", "documents", "upload",
    ])?.bodyLimit).toBe(LARGE_UPLOAD_BODY_LIMIT);
  });

  it("stops a chunked body as soon as its cumulative bytes exceed the cap", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([1, 2, 3]));
        controller.enqueue(new Uint8Array([4, 5, 6]));
        controller.close();
      },
    });
    await expect(readBodyWithLimit(body, 5)).rejects.toBeInstanceOf(BodyLimitError);
  });

  it("returns 413 before forwarding an oversized declared body", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyPost(request("/backend/api/workspaces", {
      method: "POST",
      headers: { Origin: "http://localhost", "Content-Length": String(DEFAULT_PROXY_BODY_LIMIT + 1) },
      body: "x",
    }), { params: Promise.resolve({ path: ["api", "workspaces"] }) });
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects cross-origin writes before forwarding them", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyPost(request("/backend/api/workspaces", {
      method: "POST",
      headers: { Origin: "https://attacker.example", "Content-Type": "application/json" },
      body: "{}",
    }), { params: Promise.resolve({ path: ["api", "workspaces"] }) });
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects traversal and never forwards browser-supplied authorization", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 200,
      headers: { "Set-Cookie": "upstream=untrusted", "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const blocked = await proxyPost(request("/backend/api/../metrics", {
      method: "POST",
      headers: { Origin: "http://localhost" },
      body: "x",
    }), {
      params: Promise.resolve({ path: ["api", "..", "metrics"] }),
    });
    expect(blocked.status).toBe(404);

    const response = await proxyPost(request("/backend/api/workspaces", {
      method: "POST",
      headers: { Origin: "http://localhost", Authorization: "Bearer browser-supplied" },
      body: "{}",
    }), { params: Promise.resolve({ path: ["api", "workspaces"] }) });
    const forwardedHeaders = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    expect(forwardedHeaders.has("Authorization")).toBe(false);
    expect(response.headers.has("Set-Cookie")).toBe(false);
  });

  it("strips browser-supplied trusted-principal and forwarded headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await proxyPost(request("/backend/api/workspaces", {
      method: "POST",
      headers: {
        Origin: "http://localhost",
        "Content-Type": "application/json",
        "X-Actor-ID": "spoofed-actor",
        "X-Actor-Name": "Mallory",
        "X-Actor-Roles": "admin",
        "X-Organization-ID": "spoofed-org",
        "X-Forwarded-For": "10.0.0.1",
      },
      body: "{}",
    }), { params: Promise.resolve({ path: ["api", "workspaces"] }) });

    const forwarded = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    expect(forwarded.has("x-actor-id")).toBe(false);
    expect(forwarded.has("x-actor-name")).toBe(false);
    expect(forwarded.has("x-actor-roles")).toBe(false);
    expect(forwarded.has("x-organization-id")).toBe(false);
    expect(forwarded.has("x-forwarded-for")).toBe(false);
  });

  it("rejects a state-changing write that omits Origin without an affirming sec-fetch-site", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyPost(request("/backend/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }), { params: Promise.resolve({ path: ["api", "workspaces"] }) });
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("allows an Origin-less write when sec-fetch-site affirms same-origin", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyPost(request("/backend/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
      body: "{}",
    }), { params: Promise.resolve({ path: ["api", "workspaces"] }) });
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("derives protected authorization from the HttpOnly cookie but omits it on public auth", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const token = `dls_${"b".repeat(40)}`;
    const cookie = encodeSessionCookie(token, new Date(Date.now() + 60_000).toISOString());
    const headers = { Origin: "http://localhost", Cookie: `${AUTH_COOKIE_NAME}=${cookie}`, "Content-Type": "application/json" };

    await proxyPost(request("/backend/api/workspaces", { method: "POST", headers, body: "{}" }), {
      params: Promise.resolve({ path: ["api", "workspaces"] }),
    });
    await proxyPost(request("/backend/api/auth/login", { method: "POST", headers, body: "{}" }), {
      params: Promise.resolve({ path: ["api", "auth", "login"] }),
    });

    const protectedHeaders = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
    const publicHeaders = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers);
    expect(protectedHeaders.get("Authorization")).toBe(`Bearer ${token}`);
    expect(publicHeaders.has("Authorization")).toBe(false);
    expect(protectedHeaders.has("Cookie")).toBe(false);
  });
});

describe("session cookie bridge policy", () => {
  it("rejects cross-origin and oversized writes without exposing a relay", async () => {
    const crossOrigin = await bridgePost(request("/auth/session", {
      method: "POST",
      headers: { Origin: "https://attacker.example", "Content-Type": "application/json" },
      body: "{}",
    }));
    expect(crossOrigin.status).toBe(403);

    const oversized = await bridgePost(request("/auth/session", {
      method: "POST",
      headers: { Origin: "http://localhost", "Content-Type": "application/json", "Content-Length": "4097" },
      body: "{}",
    }));
    expect(oversized.status).toBe(413);

    const originlessWrite = await bridgePost(request("/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }));
    expect(originlessWrite.status).toBe(403);
  });
});
