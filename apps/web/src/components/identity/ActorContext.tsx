"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useOptionalAuth } from "@/components/auth/AuthContext";
import type { WorkflowActor } from "@/lib/types";

export interface DemoActorProfile {
  id: string;
  name: string;
  shortName: string;
  roleLabel: string;
  roles: string[];
}

export const DEMO_ACTORS: readonly DemoActorProfile[] = [
  {
    id: "demo-associate",
    name: "Alex Morgan",
    shortName: "A. Morgan",
    roleLabel: "Associate",
    roles: ["associate"],
  },
  {
    id: "demo-principal",
    name: "Jordan Lee",
    shortName: "J. Lee",
    roleLabel: "Principal",
    roles: ["principal"],
  },
  {
    id: "demo-partner",
    name: "Priya Shah",
    shortName: "P. Shah",
    roleLabel: "Investment Partner",
    roles: ["investment_partner"],
  },
  {
    id: "demo-operating-partner",
    name: "Marcus Chen",
    shortName: "M. Chen",
    roleLabel: "Operating Partner",
    roles: ["operating_partner"],
  },
] as const;

const STORAGE_KEY = "deallens.demoActorId";

interface ActorContextValue {
  profile: DemoActorProfile;
  actor: WorkflowActor;
  profiles: readonly DemoActorProfile[];
  selectActor: (id: string) => void;
  isCurrentActor: (actorId: string | null | undefined) => boolean;
}

const ActorContext = createContext<ActorContextValue | null>(null);

export function ActorProvider({ children }: { children: ReactNode }) {
  const auth = useOptionalAuth();
  const [profileId, setProfileId] = useState(DEMO_ACTORS[0].id);

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && DEMO_ACTORS.some((profile) => profile.id === saved)) setProfileId(saved);
  }, []);

  const demoProfile = DEMO_ACTORS.find((item) => item.id === profileId) ?? DEMO_ACTORS[0];
  const authenticatedPrincipal = auth?.session?.principal;
  const profile = useMemo<DemoActorProfile>(() => {
    if (!authenticatedPrincipal) return demoProfile;
    const nameParts = authenticatedPrincipal.display_name.split(/\s+/).filter(Boolean);
    const shortName = nameParts.length > 1
      ? `${nameParts[0][0]}. ${nameParts.at(-1)}`
      : authenticatedPrincipal.display_name;
    return {
      id: authenticatedPrincipal.user_id,
      name: authenticatedPrincipal.display_name,
      shortName,
      roleLabel: authenticatedPrincipal.role.replace("_", " ").replace(/\b\w/g, (item) => item.toUpperCase()),
      roles: authenticatedPrincipal.role === "owner" || authenticatedPrincipal.role === "admin"
        ? [authenticatedPrincipal.role, "organization_admin", "integration_admin"]
        : [authenticatedPrincipal.role],
    };
  }, [authenticatedPrincipal, demoProfile]);
  const value = useMemo<ActorContextValue>(() => {
    const actor: WorkflowActor = {
      actorId: profile.id,
      actorName: profile.name,
      roles: [...profile.roles],
      organizationId: authenticatedPrincipal?.organization_id,
    };
    return {
      profile,
      actor,
      profiles: authenticatedPrincipal ? [profile] : DEMO_ACTORS,
      selectActor: (id: string) => {
        if (authenticatedPrincipal) return;
        if (!DEMO_ACTORS.some((item) => item.id === id)) return;
        window.localStorage.setItem(STORAGE_KEY, id);
        setProfileId(id);
      },
      isCurrentActor: (actorId) => Boolean(actorId && actorId === profile.id),
    };
  }, [authenticatedPrincipal, profile]);

  return <ActorContext.Provider value={value}>{children}</ActorContext.Provider>;
}

export function useActor() {
  const value = useContext(ActorContext);
  if (!value) throw new Error("useActor must be used within ActorProvider");
  return value;
}
