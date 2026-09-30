"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { login } from "@/lib/api";
import { DEMO_ACCOUNTS, allowedRoles, type Role } from "@/lib/auth";

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [username, setUsername] = useState(DEMO_ACCOUNTS[0].username);
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
      const target = next && next.startsWith("/") && allowedRoles(next)?.includes(session.role as Role) ? next : session.home;
      router.push(target);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto max-w-sm space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Sign in</h1>
        <p className="mt-1 text-sm opacity-70">Hackathon demo accounts. Choose a role.</p>
      </div>

      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">Role</legend>
        {DEMO_ACCOUNTS.map((a) => (
          <label
            key={a.username}
            className="flex cursor-pointer items-center gap-3 rounded border border-black/15 px-3 py-2 has-[:checked]:border-blue-600 has-[:checked]:bg-blue-50 dark:border-white/20 dark:has-[:checked]:bg-blue-950"
          >
            <input
              type="radio"
              name="account"
              value={a.username}
              checked={username === a.username}
              onChange={() => setUsername(a.username)}
            />
            <span>{a.label}</span>
            <span className="ml-auto font-mono text-xs opacity-60">{a.username}</span>
          </label>
        ))}
      </fieldset>

      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="w-full rounded bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-60"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
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
