import type { AuthSessionToken } from "./types";

export async function installServerAuthSession(session: AuthSessionToken) {
  const response = await fetch("/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: session.access_token, expires_at: session.expires_at }),
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Could not establish the secure browser session");
}

export async function inspectServerAuthSession(): Promise<{ authenticated: boolean; expires_at: string | null }> {
  const response = await fetch("/auth/session", { method: "GET", credentials: "same-origin", cache: "no-store" });
  if (!response.ok) return { authenticated: false, expires_at: null };
  return response.json() as Promise<{ authenticated: boolean; expires_at: string | null }>;
}

export async function clearServerAuthSession() {
  if (typeof window === "undefined") return;
  try {
    await fetch("/auth/session", { method: "DELETE", credentials: "same-origin" });
  } catch {
    // Local metadata is still cleared; an expired/revoked HttpOnly token is harmless.
  }
}
