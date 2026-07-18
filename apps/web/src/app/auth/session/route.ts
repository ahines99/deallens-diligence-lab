import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  AUTH_COOKIE_NAME,
  decodeSessionCookie,
  encodeSessionCookie,
  validOpaqueAccessToken,
} from "@/lib/sessionCookie";
import {
  BodyLimitError,
  declaredBodyExceedsLimit,
  publicOrigin,
  readBodyWithLimit,
  SESSION_BRIDGE_BODY_LIMIT,
} from "@/lib/backendProxyPolicy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Only invoked on state-changing methods (POST/DELETE). A missing Origin header
// is only trusted when the browser affirms a same-origin/direct request via
// sec-fetch-site; an absent/unknown value on a write is rejected as CSRF
// defense-in-depth on top of the SameSite=strict session cookie.
function sameOrigin(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin) {
    const secFetchSite = request.headers.get("sec-fetch-site");
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

const cookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "strict" as const,
  path: "/",
};

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "Cross-origin session writes are not allowed" }, { status: 403 });
  if (declaredBodyExceedsLimit(request.headers.get("content-length"), SESSION_BRIDGE_BODY_LIMIT)) {
    return NextResponse.json({ detail: "Session payload is too large" }, { status: 413 });
  }
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    return NextResponse.json({ detail: "Session payload must be JSON" }, { status: 415 });
  }
  let body: { access_token?: unknown; expires_at?: unknown };
  try {
    const bytes = await readBodyWithLimit(request.body, SESSION_BRIDGE_BODY_LIMIT);
    body = JSON.parse(new TextDecoder().decode(bytes));
  } catch (error) {
    if (error instanceof BodyLimitError) {
      return NextResponse.json({ detail: "Session payload is too large" }, { status: 413 });
    }
    return NextResponse.json({ detail: "Invalid session payload" }, { status: 400 });
  }
  if (!validOpaqueAccessToken(body.access_token) || typeof body.expires_at !== "string") {
    return NextResponse.json({ detail: "Invalid session payload" }, { status: 400 });
  }
  let value: string;
  try {
    value = encodeSessionCookie(body.access_token, body.expires_at);
  } catch {
    return NextResponse.json({ detail: "Invalid or expired session" }, { status: 400 });
  }
  const response = NextResponse.json({ established: true });
  response.cookies.set(AUTH_COOKIE_NAME, value, {
    ...cookieOptions,
    expires: new Date(body.expires_at),
  });
  return response;
}

export function GET(request: NextRequest) {
  const session = decodeSessionCookie(request.cookies.get(AUTH_COOKIE_NAME)?.value);
  return NextResponse.json({ authenticated: Boolean(session), expires_at: session?.expiresAt ?? null });
}

export function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "Cross-origin session writes are not allowed" }, { status: 403 });
  const response = NextResponse.json({ cleared: true });
  response.cookies.set(AUTH_COOKIE_NAME, "", { ...cookieOptions, maxAge: 0 });
  return response;
}
