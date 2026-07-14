"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { useAuth } from "@/components/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Field, TextInput } from "@/components/workbench/Primitives";

export default function LoginPage() {
  const router = useRouter();
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationId, setOrganizationId] = useState("");

  useEffect(() => {
    if (auth.status === "authenticated") router.replace("/portfolio");
  }, [auth.status, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    auth.clearError();
    try {
      await auth.login({
        email: email.trim().toLowerCase(),
        password,
        organization_id: organizationId.trim() || undefined,
      });
      router.replace("/portfolio");
    } catch {
      // The context exposes the server-safe error detail without retaining credentials.
    }
  }

  return (
    <AuthShell
      eyebrow="Secure access"
      title="Sign in to DealLens"
      subtitle="Continue into your firm's tenant-scoped underwriting and portfolio workspace."
      footer={<>New to DealLens? <Link href="/register" className="font-semibold text-accent hover:underline">Create a firm account</Link></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Work email"><TextInput type="email" autoComplete="email" required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="analyst@firm.com" /></Field>
        <Field label="Password"><TextInput type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={(event) => setPassword(event.target.value)} /></Field>
        <details className="rounded border border-line bg-panel2 px-3 py-2.5">
          <summary className="cursor-pointer text-xs font-semibold text-muted">Multi-organization account</summary>
          <div className="mt-3"><Field label="Organization ID" hint="Required only when prompted"><TextInput value={organizationId} onChange={(event) => setOrganizationId(event.target.value)} minLength={32} maxLength={32} pattern="[a-zA-Z0-9]{32}" autoComplete="off" placeholder="32-character organization ID" className="font-mono" /></Field></div>
        </details>
        {auth.error && <div role="alert" className="rounded border border-[#e5c9c3] bg-[#fbf1ef] px-3 py-2.5 text-xs leading-relaxed text-negative">{auth.error}</div>}
        <Button type="submit" disabled={auth.busy || auth.status === "loading"} className="w-full">{auth.busy ? "Signing in…" : "Sign in"}</Button>
        <p className="text-center text-2xs leading-relaxed text-faint">The opaque session is held in a same-origin HttpOnly cookie and expires automatically; no bearer is stored in page-accessible storage.</p>
      </form>
    </AuthShell>
  );
}
