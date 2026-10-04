"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { AuthMe } from "@/lib/facilityScope";
import { createRequestGate, type RequestGate } from "@/lib/requestGate";
import { POLL_MS, clockOffsetMs, type CaseReview, type Queue } from "@/lib/review";

/** One shared 1-second clock for every countdown on the page (one interval, not one per row). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export type Loadable<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** HTTP status of the last failure (401 session ended, 403 no access), or null for none / network / timeout. */
  errorStatus: number | null;
  /** True when the last refresh failed but earlier data is still shown (never cleared on error). */
  stale: boolean;
  /** Server clock minus client clock, from the last successful response. */
  offsetMs: number;
  /** Client time of the last successful response. */
  receivedAt: number | null;
  reload: () => Promise<void>;
};

function message(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Your session has ended. Sign in again.";
    if (err.status === 403) return "This account does not have access to this view.";
    return `${err.message}${err.requestId ? ` (reference ${err.requestId})` : ""}`;
  }
  return "The server could not be reached.";
}

const TIMEOUT_MS = 20_000;

/** Polls a server resource that carries `generated_at`. Keeps the last good data on error (shown as stale, never
 * cleared), skips polls while `paused` (an open dialog) or while the tab is hidden, and refreshes on return. Requests
 * go through a latest-wins gate (lib/requestGate.ts): a newer reload, a path change or unmount aborts the in-flight
 * request, and a 20 s timeout aborts a hung one, so a late answer never overwrites newer data. Background polls skip
 * while a request is still in flight (no pile-up, no retry storm); there is no automatic retry beyond the interval.
 * `keepOnPathChange` keeps showing the previous data while a new query loads (the queue with a different filter)
 * instead of blanking the screen. */
export function usePolled<T extends { generated_at: string }>(path: string | null, paused = false, keepOnPathChange = false): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [offsetMs, setOffset] = useState(0);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const gateRef = useRef<RequestGate | null>(null);
  if (gateRef.current === null) gateRef.current = createRequestGate(TIMEOUT_MS);
  const pathRef = useRef(path);
  pathRef.current = path;

  const load = useCallback(async (background: boolean) => {
    const p = pathRef.current;
    const gate = gateRef.current!;
    if (!p) return;
    if (background && gate.inFlight()) return; // the previous poll is still running; its timeout will end it
    setLoading(true);
    const out = await gate.run((signal) => api.get<T>(p, { signal }));
    if (out.status === "ignored") return; // superseded or cancelled: a newer request owns the loading state
    setLoading(false);
    if (out.status === "ok") {
      const at = Date.now();
      setData(out.value);
      setOffset(clockOffsetMs(out.value.generated_at, at));
      setReceivedAt(at);
      setError(null);
      setErrorStatus(null);
    } else if (out.status === "timeout") {
      setError("The server did not answer in time.");
      setErrorStatus(null);
    } else {
      setError(message(out.error));
      setErrorStatus(out.error instanceof ApiError ? out.error.status : null);
    }
  }, []);

  const reload = useCallback(() => load(false), [load]);

  useEffect(() => {
    const gate = gateRef.current!;
    if (!keepOnPathChange) setData(null);
    setError(null);
    setErrorStatus(null);
    if (path) void load(false);
    else setLoading(false);
    return () => gate.cancel(); // path change or unmount: abort and drop the in-flight answer
  }, [path, load, keepOnPathChange]);

  useEffect(() => {
    if (!path || paused) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
    };
  }, [path, paused, load]);

  return { data, error, errorStatus, loading, stale: error !== null && data !== null, offsetMs, receivedAt, reload };
}

export function useQueue(includeSignedOff: boolean, paused: boolean) {
  return usePolled<Queue>(`triage/queue?include_signed_off=${includeSignedOff}`, paused, true);
}

export function useCaseReview(caseId: string | null, paused: boolean) {
  return usePolled<CaseReview>(caseId ? `triage/${caseId}` : null, paused);
}

/** The signed-in account from GET /auth/me (role, plus facility scope/isolation on newer servers). `null` while
 * loading; `failed` when the check did not succeed (the bar then says the role is unknown, never guesses). */
export function useMe(): { me: AuthMe | null; failed: boolean } {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const ctrl = new AbortController();
    api.get<AuthMe>("auth/me", { signal: ctrl.signal }).then(setMe, () => {
      if (!ctrl.signal.aborted) setFailed(true);
    });
    return () => ctrl.abort();
  }, []);
  return { me, failed };
}
