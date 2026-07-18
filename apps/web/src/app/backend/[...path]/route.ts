import type { NextRequest } from "next/server";
import { AUTH_COOKIE_NAME, authorizationFromSessionCookie } from "@/lib/sessionCookie";
import {
  BodyLimitError,
  declaredBodyExceedsLimit,
  publicOrigin,
  readBodyWithLimit,
  resolveProxyPolicy,
} from "@/lib/backendProxyPolicy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const METHODS_WITHOUT_BODY = new Set(["GET", "HEAD"]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const PUBLIC_UPSTREAM_PATHS = new Set([
  "api/health",
  "api/auth/login",
  "api/auth/register",
  "api/auth/demo",
]);
const REQUEST_HEADERS_TO_STRIP = [
  "authorization",
  "connection",
  "content-length",
  "cookie",
  "forwarded",
  "host",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "via",
  // Trusted-principal headers: the backend derives the actor context server-side
  // (serverApi/workflowHeaders on SSR calls, which reach the API directly — not
  // through this proxy). A browser must never be able to supply them through the
  // BFF, otherwise an auth-off deployment would gain an impersonation vector.
  "x-actor-id",
  "x-actor-name",
  "x-actor-roles",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-port",
  "x-forwarded-proto",
  "x-organization-id",
];

function tooLarge(limit: number) {
  return Response.json(
    { detail: `Request body exceeds the ${limit}-byte limit.` },
    { status: 413 },
  );
}

function hasTrustedOrigin(request: NextRequest) {
  if (SAFE_METHODS.has(request.method)) return true;
  const secFetchSite = request.headers.get("sec-fetch-site");
  if (secFetchSite === "cross-site") return false;
  const origin = request.headers.get("origin");
  if (!origin) {
    // A state-changing request with no Origin header is only trusted when the
    // browser affirms it is same-origin (or a direct navigation via 'none').
    // An absent/unknown sec-fetch-site on a write is rejected as CSRF
    // defense-in-depth; GET/HEAD stay permissive via SAFE_METHODS above.
    return secFetchSite === "same-origin" || secFetchSite === "none";
  }
  try {
    return new URL(origin).origin === publicOrigin({
      forwardedProto: request.headers.get("x-forwarded-proto"),
      forwardedHost: request.headers.get("x-forwarded-host"),
      host: request.headers.get("host"),
      fallbackOrigin: request.nextUrl.origin,
    });
  } catch {
    return false;
  }
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (!hasTrustedOrigin(request)) {
    return Response.json({ detail: "Cross-origin backend writes are not allowed." }, { status: 403 });
  }
  const { path } = await context.params;
  const policy = resolveProxyPolicy(path);
  if (!policy) {
    return Response.json({ detail: "The backend proxy only accepts normalized API paths." }, { status: 404 });
  }
  if (declaredBodyExceedsLimit(request.headers.get("content-length"), policy.bodyLimit)) {
    return tooLarge(policy.bodyLimit);
  }

  const apiBase = (
    process.env.API_URL_INTERNAL ||
    process.env.SERVER_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000"
  ).replace(/\/$/, "");
  const upstreamPath = policy.upstreamPath;
  const upstreamUrl = new URL(`${apiBase}/${upstreamPath}`);
  upstreamUrl.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  REQUEST_HEADERS_TO_STRIP.forEach((header) => headers.delete(header));
  if (!PUBLIC_UPSTREAM_PATHS.has(upstreamPath)) {
    const authorization = authorizationFromSessionCookie(
      request.cookies.get(AUTH_COOKIE_NAME)?.value,
    );
    if (authorization) headers.set("Authorization", authorization);
  }

  try {
    const body = METHODS_WITHOUT_BODY.has(request.method)
      ? undefined
      : await readBodyWithLimit(request.body, policy.bodyLimit);
    const response = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
    });
    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    responseHeaders.delete("set-cookie");
    responseHeaders.delete("transfer-encoding");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    if (error instanceof BodyLimitError) return tooLarge(error.limit);
    return Response.json(
      { detail: "Cannot reach the internal API." },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
export const HEAD = proxy;
