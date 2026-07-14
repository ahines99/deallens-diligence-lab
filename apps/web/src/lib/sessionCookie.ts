export const AUTH_COOKIE_NAME = "deallens_auth_session";

export interface OpaqueSessionCookie {
  accessToken: string;
  expiresAt: string;
}

export function validOpaqueAccessToken(value: unknown): value is string {
  return typeof value === "string" && value.startsWith("dls_") && value.length >= 32 && value.length <= 512;
}

export function encodeSessionCookie(accessToken: string, expiresAt: string) {
  if (!validOpaqueAccessToken(accessToken)) throw new Error("Invalid opaque access token");
  const expires = Date.parse(expiresAt);
  if (!Number.isFinite(expires) || expires <= Date.now()) throw new Error("Invalid session expiry");
  return `${expires}.${accessToken}`;
}

export function decodeSessionCookie(value: string | undefined | null): OpaqueSessionCookie | null {
  if (!value) return null;
  const separator = value.indexOf(".");
  if (separator < 1) return null;
  const expires = Number(value.slice(0, separator));
  const accessToken = value.slice(separator + 1);
  if (!Number.isFinite(expires) || expires <= Date.now() || !validOpaqueAccessToken(accessToken)) return null;
  return { accessToken, expiresAt: new Date(expires).toISOString() };
}

export function authorizationFromSessionCookie(value: string | undefined | null) {
  const session = decodeSessionCookie(value);
  return session ? `Bearer ${session.accessToken}` : null;
}
