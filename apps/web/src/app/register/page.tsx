"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { useAuth } from "@/components/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Field, TextInput } from "@/components/workbench/Primitives";

function slugify(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export default function RegisterPage() {
  const router = useRouter();
  const auth = useAuth();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [organizationSlug, setOrganizationSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (auth.status === "authenticated") router.replace("/portfolio");
  }, [auth.status, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    auth.clearError();
    setLocalError(null);
    if (password !== confirmation) {
      setLocalError("Passwords do not match.");
      return;
    }
    try {
      await auth.register({
        display_name: displayName.trim(),
        email: email.trim().toLowerCase(),
        password,
        organization_name: organizationName.trim(),
        organization_slug: organizationSlug,
      });
      router.replace("/portfolio");
    } catch {
      // The context exposes a normalized server error and never retains the password.
    }
  }

  return (
    <AuthShell
      eyebrow="Firm onboarding"
      title="Create your organization"
      subtitle="The first account becomes the organization owner and can govern data policy and membership access."
      footer={<>Already have an account? <Link href="/login" className="font-semibold text-accent hover:underline">Sign in</Link></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Full name"><TextInput required maxLength={200} autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Jordan Lee" /></Field>
          <Field label="Work email"><TextInput required maxLength={320} type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="jordan@firm.com" /></Field>
          <Field label="Organization name"><TextInput required maxLength={200} autoComplete="organization" value={organizationName} onChange={(event) => { const value = event.target.value; setOrganizationName(value); if (!slugEdited) setOrganizationSlug(slugify(value)); }} placeholder="Northbridge Capital" /></Field>
          <Field label="Organization slug"><TextInput required minLength={2} maxLength={100} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={organizationSlug} onChange={(event) => { setSlugEdited(true); setOrganizationSlug(event.target.value.toLowerCase()); }} placeholder="northbridge-capital" className="font-mono" /></Field>
          <Field label="Password" hint="12+ characters"><TextInput required minLength={12} maxLength={256} type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></Field>
          <Field label="Confirm password"><TextInput required minLength={12} maxLength={256} type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></Field>
        </div>
        {(localError || auth.error) && <div role="alert" className="rounded border border-[#e5c9c3] bg-[#fbf1ef] px-3 py-2.5 text-xs leading-relaxed text-negative">{localError || auth.error}</div>}
        <Button type="submit" disabled={auth.busy || auth.status === "loading"} className="w-full">{auth.busy ? "Creating organization…" : "Create organization"}</Button>
        <p className="text-center text-2xs leading-relaxed text-faint">By continuing, you accept responsibility for access, source rights, and human review of investment outputs.</p>
      </form>
    </AuthShell>
  );
}
