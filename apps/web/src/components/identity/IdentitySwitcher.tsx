"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth, useOptionalAuth } from "@/components/auth/AuthContext";
import { useActor } from "./ActorContext";

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function roleLabel(role: string) {
  return role.replace("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

export function IdentitySwitcher() {
  const auth = useOptionalAuth();
  const { profile, profiles, selectActor } = useActor();

  if (auth?.status === "loading") {
    return <div className="ml-auto h-8 w-32 animate-pulse rounded border border-white/10 bg-white/[0.06]" aria-label="Restoring session" />;
  }

  if (auth?.status === "authenticated" && auth.session) return <AuthenticatedIdentity />;

  return (
    <div className="ml-auto flex min-w-0 items-center gap-2">
      <label className="flex min-w-0 items-center gap-2 rounded border border-white/15 bg-white/[0.06] px-2 py-1 text-white">
        <span className="hidden text-2xs uppercase tracking-eyebrow text-white/45 xl:inline">Demo actor</span>
        <span className="h-6 w-6 shrink-0 rounded-full bg-white/10 text-center text-[0.62rem] font-semibold leading-6 text-white/80" aria-hidden>{initials(profile.name)}</span>
        <select
          aria-label="Acting identity"
          value={profile.id}
          onChange={(event) => selectActor(event.target.value)}
          className="max-w-[7rem] bg-transparent py-0.5 text-xs font-medium text-white outline-none sm:max-w-[10rem] [&>option]:text-ink"
        >
          {profiles.map((item) => <option key={item.id} value={item.id}>{item.shortName} · {item.roleLabel}</option>)}
        </select>
      </label>
      <Link href="/login" className="rounded px-2 py-1.5 text-xs font-semibold text-white/75 transition hover:bg-white/10 hover:text-white">Sign in</Link>
    </div>
  );
}

function AuthenticatedIdentity() {
  const router = useRouter();
  const auth = useAuth();
  if (!auth.session) return null;
  const { principal, memberships } = auth.session;
  return (
      <details className="group relative ml-auto">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded border border-white/15 bg-white/[0.06] px-2 py-1 text-white transition hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-white/30 [&::-webkit-details-marker]:hidden">
          <span className="h-6 w-6 shrink-0 rounded-full bg-white/10 text-center text-[0.62rem] font-semibold leading-6 text-white/85" aria-hidden>{initials(principal.display_name)}</span>
          <span className="hidden min-w-0 sm:block">
            <span className="block max-w-32 truncate text-xs font-semibold leading-tight">{principal.display_name}</span>
            <span className="block text-[9px] uppercase tracking-wide text-white/45">{roleLabel(principal.role)}</span>
          </span>
          <span className="text-[9px] text-white/40 transition group-open:rotate-180" aria-hidden>▼</span>
        </summary>
        <div className="absolute right-0 top-[calc(100%+0.55rem)] z-50 w-[19rem] rounded-md border border-line-strong bg-panel p-4 text-body shadow-md">
          <div className="border-b border-line pb-3">
            <div className="flex items-center gap-3">
              <span className="h-9 w-9 rounded-full bg-accent-soft text-center text-xs font-bold leading-9 text-accent">{initials(principal.display_name)}</span>
              <div className="min-w-0"><p className="truncate text-sm font-semibold text-ink">{principal.display_name}</p><p className="truncate text-2xs text-muted">{principal.email}</p></div>
            </div>
            <span className="mt-2 inline-flex rounded-sm bg-sunken px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">{roleLabel(principal.role)}</span>
          </div>
          <label className="mt-3 block">
            <span className="mb-1.5 block text-2xs font-semibold uppercase tracking-eyebrow text-muted">Active organization</span>
            <select
              value={principal.organization_id}
              disabled={auth.busy || memberships.length < 2}
              onChange={(event) => {
                void auth.switchOrganization(event.target.value).then(() => {
                  router.push("/portfolio");
                }).catch(() => undefined);
              }}
              className="w-full rounded border border-line-strong bg-panel px-2.5 py-2 text-xs text-ink focus:border-accent focus:outline-none disabled:bg-panel2 disabled:text-muted"
            >
              {memberships.map((membership) => (
                <option key={membership.id} value={membership.organization_id}>
                  {auth.organizationLabel(membership.organization_id)} · {roleLabel(membership.role)}
                </option>
              ))}
            </select>
            {memberships.length < 2 && <span className="mt-1.5 block text-[10px] text-faint">No other active organization memberships.</span>}
          </label>
          {auth.error && <p role="alert" className="mt-2 text-2xs leading-relaxed text-negative">{auth.error}</p>}
          <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
            <Link href="/portfolio" className="text-xs font-semibold text-accent hover:underline">Portfolio</Link>
            <button
              type="button"
              disabled={auth.busy}
              onClick={() => { void auth.logout().then(() => router.push("/login")); }}
              className="rounded border border-line-strong px-2.5 py-1.5 text-xs font-semibold text-ink transition hover:bg-panel2 disabled:opacity-50"
            >
              {auth.busy ? "Working…" : "Sign out"}
            </button>
          </div>
        </div>
      </details>
  );
}
