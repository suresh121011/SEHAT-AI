"use client";

// Server reachability from GET /health (via the same-origin proxy). It reports only what that endpoint says: the API
// answered, and whether its database tables are present. It is not a claim that "all systems" (AI, OCR, voice) work.
// Teal / amber, never green or red: those colours mean clinical urgency.
import { useEffect, useState } from "react";

type State = "checking" | "ok" | "degraded" | "down";

const TEXT: Record<State, string> = {
  checking: "Checking server…",
  ok: "Server reachable",
  degraded: "Server reachable · database issue",
  down: "Server not reachable",
};

export function ServerStatus({ compact = false, onShell = false }: { compact?: boolean; onShell?: boolean }) {
  const [state, setState] = useState<State>("checking");

  useEffect(() => {
    let alive = true;
    async function check() {
      const ctrl = new AbortController();
      const t = window.setTimeout(() => ctrl.abort(), 8000);
      try {
        const res = await fetch("/api/backend/health", { cache: "no-store", signal: ctrl.signal });
        const body = res.ok ? await res.json().catch(() => null) : null;
        if (alive) setState(!res.ok ? "down" : body?.database === "ok" ? "ok" : "degraded");
      } catch {
        if (alive) setState("down");
      } finally {
        window.clearTimeout(t);
      }
    }
    void check();
    const id = window.setInterval(check, 60_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const dot = state === "ok" ? (onShell ? "bg-[#7fd1c7]" : "bg-primary") : state === "checking" ? "bg-line" : "bg-[#e0a526]";
  return (
    <span role="status" className={`inline-flex items-center gap-2 text-sm ${onShell ? "text-shell-muted" : "text-muted"}`} title={TEXT[state]}>
      <span className={`size-2.5 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
      <span className={compact ? "sr-only" : ""}>{TEXT[state]}</span>
    </span>
  );
}
