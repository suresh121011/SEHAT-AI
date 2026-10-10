"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { Icon, type IconName } from "@/components/Icon";
import { Wordmark } from "@/components/shell/Brand";
import { Button, Notice } from "@/components/ui";
import { login } from "@/lib/api";
import { DEMO_ACCOUNTS, allowedRoles, type Role } from "@/lib/auth";

const ROLE_INFO: Partial<Record<Role, { text: string; icon: IconName; lands: string }>> = {
  patient: { text: "Start a case and describe symptoms yourself.", icon: "people", lands: "Patient intake" },
  anm: { text: "Assisted mode: guide a patient through every step and submit triage.", icon: "clipboard", lands: "Patient intake" },
  medical_officer: { text: "Review the priority queue, check evidence and sign off each case.", icon: "stethoscope", lands: "Overview and review workstation" },
  supervisor: { text: "Read-only queue and governance aggregates.", icon: "chart", lands: "Governance" },
};

const PRINCIPLES: { icon: IconName; title: string; text: string }[] = [
  { icon: "scale", title: "Fixed rules set urgency", text: "A deterministic rules engine decides RED, YELLOW or GREEN. AI never lowers it." },
  { icon: "sparkle", title: "AI only extracts and summarises", text: "Every AI-extracted value is labelled and must be checked by a person." },
  { icon: "people", title: "A medical officer signs off", text: "Each result is reviewed by a named role, with the source of every value." },
];

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
    <form onSubmit={onSubmit} className="w-full max-w-xl space-y-6">
      <div className="lg:hidden">
        <Wordmark subtitle="Clinical triage · research prototype" />
      </div>
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Sign in</h1>
        <p className="text-base text-muted">Shared demo accounts for the hackathon. Choose who is using the screen.</p>
      </div>

      <DemoBanner />

      <fieldset>
        <legend className="mb-3 font-bold">Who is using SEHAT AI?</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {DEMO_ACCOUNTS.map((a) => {
            const info = ROLE_INFO[a.role];
            const selected = username === a.username;
            return (
              <label
                key={a.username}
                className={`relative flex cursor-pointer flex-col gap-2 rounded-xl border bg-card p-4 transition-[border-color,box-shadow] duration-150 has-[:focus-visible]:outline has-[:focus-visible]:outline-3 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus ${selected ? "border-primary shadow-[0_0_0_1px_var(--primary)]" : "border-subtle shadow-card hover:border-strong"}`}
              >
                <input type="radio" name="account" value={a.username} checked={selected} onChange={() => setUsername(a.username)} className="sr-only" />
                <span className="flex items-start justify-between gap-2">
                  <span className={`inline-flex size-10 items-center justify-center rounded-lg ${selected ? "bg-primary text-white" : "bg-primary-tint text-primary"}`}>
                    {info && <Icon name={info.icon} size={20} />}
                  </span>
                  <span className={`inline-flex size-5 items-center justify-center rounded-full border ${selected ? "border-primary bg-primary text-white" : "border-line bg-card"}`} aria-hidden="true">
                    {selected && <Icon name="check" size={14} />}
                  </span>
                </span>
                <span className="font-bold text-ink">{a.label}</span>
                {info && <span className="text-sm leading-5 text-muted">{info.text}</span>}
                <span className="mt-auto flex flex-wrap items-center justify-between gap-1 border-t border-subtle pt-2 text-xs text-muted">
                  <span className="font-mono">{a.username}</span>
                  {info && <span>Opens: {info.lands}</span>}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {error && (
        <Notice tone="error" role="alert">
          {error}
        </Notice>
      )}

      <Button type="submit" disabled={busy} className="w-full">
        {busy ? "Signing in…" : "Sign in"} {!busy && <Icon name="arrowRight" size={18} />}
      </Button>
      <p className="text-center text-xs text-muted">No password: demo accounts are shared and hold synthetic data only.</p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <aside className="on-shell relative hidden overflow-hidden bg-shell px-10 py-10 text-shell-ink lg:flex lg:flex-col">
        <div aria-hidden="true" className="pointer-events-none absolute -right-24 -top-24 size-80 rounded-full bg-white/[0.04]" />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-32 -left-16 size-96 rounded-full bg-white/[0.03]" />
        <Wordmark onShell subtitle="Clinical triage · research prototype" />
        <div className="relative mt-auto max-w-md space-y-8 py-10">
          <div className="space-y-3">
            <p className="text-3xl font-bold leading-tight tracking-tight">Rules-first triage and handoff for Indian healthcare facilities.</p>
            <p className="text-shell-muted">Voice, documents and images help collect information. The decision on urgency stays with fixed rules and a medical officer.</p>
          </div>
          <ul className="space-y-4">
            {PRINCIPLES.map((p) => (
              <li key={p.title} className="flex gap-3">
                <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg bg-white/10">
                  <Icon name={p.icon} size={20} />
                </span>
                <span>
                  <span className="block font-bold">{p.title}</span>
                  <span className="block text-sm text-shell-muted">{p.text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-shell-muted">Research prototype, not a clinically validated device. Non-diagnostic. Synthetic data only.</p>
      </aside>
      <main id="main" className="flex items-start justify-center bg-page px-4 py-8 sm:px-8 lg:items-center lg:py-12">
        <Suspense>
          <LoginForm />
        </Suspense>
      </main>
    </div>
  );
}
