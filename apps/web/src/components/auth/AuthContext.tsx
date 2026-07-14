"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import {
  AUTH_SESSION_EVENT,
  clearAuthSession,
  readAuthSession,
  saveAuthSession,
} from "@/lib/authSession";
import {
  clearServerAuthSession,
  inspectServerAuthSession,
  installServerAuthSession,
} from "@/lib/authBridge";
import type {
  BrowserAuthSession,
  LoginInput,
  RegistrationInput,
} from "@/lib/types";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: AuthStatus;
  session: BrowserAuthSession | null;
  busy: boolean;
  error: string | null;
  login: (input: LoginInput) => Promise<BrowserAuthSession>;
  startDemo: () => Promise<BrowserAuthSession>;
  register: (input: RegistrationInput) => Promise<BrowserAuthSession>;
  logout: () => Promise<void>;
  switchOrganization: (organizationId: string) => Promise<BrowserAuthSession>;
  organizationLabel: (organizationId: string) => string;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function message(caught: unknown, fallback: string) {
  return caught instanceof ApiError ? caught.message : fallback;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<BrowserAuthSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [organizationNames, setOrganizationNames] = useState<Record<string, string>>({});

  const resolveCurrentOrganization = useCallback(async (organizationId: string) => {
    try {
      const organizations = await api.listOrganizations();
      const current = organizations.find((item) => item.id === organizationId);
      if (current) {
        setOrganizationNames((names) => ({ ...names, [current.id]: current.name }));
      }
    } catch {
      // Memberships remain switchable by stable identifier when the optional name lookup fails.
    }
  }, []);

  useEffect(() => {
    let active = true;
    async function bootstrap() {
      const stored = readAuthSession();
      try {
        const bridge = await inspectServerAuthSession();
        if (!bridge.authenticated || !bridge.expires_at) throw new Error("No secure session");
        const identity = await api.currentIdentity();
        if (!active) return;
        const refreshed = saveAuthSession({
          token_type: "bearer",
          expires_at: bridge.expires_at,
          ...identity,
        });
        setSession(refreshed);
        setStatus("authenticated");
        void resolveCurrentOrganization(refreshed.principal.organization_id);
      } catch {
        if (!active) return;
        if (stored) clearAuthSession();
        setSession(null);
        setStatus("anonymous");
      }
    }
    void bootstrap();
    return () => { active = false; };
  }, [resolveCurrentOrganization]);

  useEffect(() => {
    function synchronize() {
      const next = readAuthSession();
      setSession(next);
      setStatus(next ? "authenticated" : "anonymous");
    }
    window.addEventListener(AUTH_SESSION_EVENT, synchronize);
    return () => window.removeEventListener(AUTH_SESSION_EVENT, synchronize);
  }, []);

  const login = useCallback(async (input: LoginInput) => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.login(input);
      await installServerAuthSession(next);
      const browserSession = saveAuthSession(next);
      setSession(browserSession);
      setStatus("authenticated");
      void resolveCurrentOrganization(browserSession.principal.organization_id);
      return browserSession;
    } catch (caught) {
      const detail = message(caught, "Could not sign in.");
      setError(detail);
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [resolveCurrentOrganization]);

  const startDemo = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.startDemoSession();
      await installServerAuthSession(next);
      const browserSession = saveAuthSession(next);
      setSession(browserSession);
      setStatus("authenticated");
      setOrganizationNames((names) => ({
        ...names,
        [browserSession.principal.organization_id]: "Demo Sandbox",
      }));
      return browserSession;
    } catch (caught) {
      const detail = message(caught, "Could not start a demo session.");
      setError(detail);
      throw caught;
    } finally {
      setBusy(false);
    }
  }, []);

  const register = useCallback(async (input: RegistrationInput) => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.register(input);
      await installServerAuthSession(next);
      const browserSession = saveAuthSession(next);
      setSession(browserSession);
      setStatus("authenticated");
      setOrganizationNames((names) => ({
        ...names,
        [browserSession.principal.organization_id]: input.organization_name,
      }));
      return browserSession;
    } catch (caught) {
      const detail = message(caught, "Could not create the account.");
      setError(detail);
      throw caught;
    } finally {
      setBusy(false);
    }
  }, []);

  const logout = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      if (readAuthSession()) await api.logout();
    } catch (caught) {
      setError(message(caught, "The server could not confirm logout; the local session was cleared."));
    } finally {
      await clearServerAuthSession();
      clearAuthSession();
      setSession(null);
      setStatus("anonymous");
      setBusy(false);
    }
  }, []);

  const switchOrganization = useCallback(async (organizationId: string) => {
    if (!session) throw new Error("An authenticated session is required");
    if (organizationId === session.principal.organization_id) return session;
    setBusy(true);
    setError(null);
    try {
      const next = await api.switchOrganization(organizationId);
      await installServerAuthSession(next);
      const browserSession = saveAuthSession(next);
      setSession(browserSession);
      setStatus("authenticated");
      void resolveCurrentOrganization(browserSession.principal.organization_id);
      return browserSession;
    } catch (caught) {
      const detail = message(caught, "Could not switch organizations.");
      setError(detail);
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [resolveCurrentOrganization, session]);

  const value = useMemo<AuthContextValue>(() => ({
    status,
    session,
    busy,
    error,
    login,
    startDemo,
    register,
    logout,
    switchOrganization,
    organizationLabel: (organizationId) => organizationNames[organizationId]
      ?? `Organization …${organizationId.slice(-6)}`,
    clearError: () => setError(null),
  }), [busy, error, login, logout, organizationNames, register, session, startDemo, status, switchOrganization]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}

export function useOptionalAuth() {
  return useContext(AuthContext);
}
