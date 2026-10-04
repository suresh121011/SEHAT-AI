"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { Icon, type IconName } from "@/components/Icon";
import { Button, Notice } from "@/components/ui";
import { login } from "@/lib/api";
import { DEMO_ACCOUNTS, allowedRoles, type Role } from "@/lib/auth";

const ROLE_INFO: Partial<Record<Role, { text: string; icon: IconName }>> = {
  patient: { text: "Start a case and describe symptoms yourself.", icon: "people" },
  anm: { text: "Assisted mode: guide a patient through every step and submit triage.", icon: "clipboard" },
  medical_officer: { text: "Clinician view (dashboard, Phase 8).", icon: "shield" },
  supervisor: { text: "Governance and audit view.", icon: "lock" },
};

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [username, setUsername] = useState(DEMO_ACCOUNTS[1].username);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const account = DEMO_ACCOUNTS.find((a) => a.username === username)!;
    setBusy(true);
    setError(null);
    try {
      const session = await login(account.username, account.role);
      // Honour ?next= only if this role may open it; otherwise go to the role's home.
      const target = next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") && allowedRoles(next)?.includes(session.role as Role) ? next : session.home;
      router.push(target);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto max-w-lg space-y-6">
      <DemoBanner />
      <div>
        <h1 className="text-3xl font-bold">Sign in</h1>
        <p className="mt-1 text-lg text-muted">Shared demo accounts for the hackathon. Choose who is using the screen.</p>
      </div>

      <fieldset className="space-y-2">
        <legend className="mb-2 font-bold">Who is using SEHAT AI?</legend>
        {DEMO_ACCOUNTS.map((a) => {
          const info = ROLE_INFO[a.role];
          const selected = username === a.username;
          return (
            <label key={a.username} className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border-2 bg-card p-4 ${selected ? "border-primary bg-primary-tint" : "border-subtle hover:border-line"}`}>
              <input type="radio" name="account" value={a.username} checked={selected} onChange={() => setUsername(a.username)} className="mt-1 size-5" />
              <span className="flex-1">
                <span className="flex items-center gap-2 font-bold">
                  {info && <Icon name={info.icon} className="text-primary" />} {a.label}
                </span>
                {info && <span className="block text-muted">{info.text}</span>}
              </span>
              <span className="font-mono text-sm text-muted">{a.username}</span>
            </label>
          );
        })}
      </fieldset>

      {error && (
        <Notice tone="error" role="alert">
          {error}
        </Notice>
      )}

      <Button type="submit" disabled={busy} className="w-full">
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
