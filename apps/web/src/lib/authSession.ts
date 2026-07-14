import type { AuthSessionToken, BrowserAuthSession } from "./types";

const SESSION_KEY = "deallens.auth.identity.v1";
const LEGACY_SESSION_KEY = "deallens.auth.session.v1";
export const AUTH_SESSION_EVENT = "deallens:auth-session-change";

function browserStorage() {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function validSession(value: unknown): value is BrowserAuthSession {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<BrowserAuthSession>;
  return Boolean(
    candidate.token_type === "bearer"
      && typeof candidate.expires_at === "string"
      && Number.isFinite(Date.parse(candidate.expires_at))
      && Date.parse(candidate.expires_at) > Date.now()
      && candidate.principal
      && typeof candidate.principal.user_id === "string"
      && typeof candidate.principal.organization_id === "string"
      && typeof candidate.principal.role === "string"
      && Array.isArray(candidate.memberships),
  );
}

function notifySessionChange() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_SESSION_EVENT));
}

export function readAuthSession(): BrowserAuthSession | null {
  const storage = browserStorage();
  if (!storage) return null;
  try {
    storage.removeItem(LEGACY_SESSION_KEY);
    const serialized = storage.getItem(SESSION_KEY);
    if (!serialized) return null;
    const parsed: unknown = JSON.parse(serialized);
    if (!validSession(parsed)) {
      storage.removeItem(SESSION_KEY);
      return null;
    }
    return parsed;
  } catch {
    try { storage.removeItem(SESSION_KEY); } catch { /* no-op */ }
    return null;
  }
}

export function saveAuthSession(session: AuthSessionToken | BrowserAuthSession): BrowserAuthSession {
  const sanitized: BrowserAuthSession = {
    token_type: session.token_type,
    expires_at: session.expires_at,
    principal: session.principal,
    memberships: session.memberships,
  };
  if (!validSession(sanitized)) throw new Error("Cannot persist invalid or expired identity metadata");
  const storage = browserStorage();
  if (storage) {
    storage.removeItem(LEGACY_SESSION_KEY);
    storage.setItem(SESSION_KEY, JSON.stringify(sanitized));
  }
  notifySessionChange();
  return sanitized;
}

export function clearAuthSession() {
  const storage = browserStorage();
  storage?.removeItem(SESSION_KEY);
  storage?.removeItem(LEGACY_SESSION_KEY);
  notifySessionChange();
}
