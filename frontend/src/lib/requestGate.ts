// Latest-wins request gate for polled reads (no React, no DOM; unit-tested with `node --test`).
// Each `run` aborts the request it supersedes, aborts itself after a timeout, and reports its result only while it is
// still the newest request. A superseded or cancelled request reports "ignored", so its late answer can never
// overwrite newer data. The gate never retries: the caller's poll interval is the only repeat.

export type AbortKind = "superseded" | "timeout" | "cancelled";

/** The abort reason passed to the AbortController, so the caller can tell why a request stopped. */
export class RequestAborted extends Error {
  kind: AbortKind;
  constructor(kind: AbortKind) {
    super(`Request ${kind}`);
    this.kind = kind;
    this.name = "AbortError";
  }
}

export type GateOutcome<T> =
  | { status: "ok"; value: T }
  | { status: "timeout" }
  | { status: "error"; error: unknown }
  /** Superseded by a newer request, or cancelled (path change, unmount): drop silently. */
  | { status: "ignored" };

type Timers = { set: (fn: () => void, ms: number) => unknown; clear: (handle: unknown) => void };

const defaultTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export type RequestGate = {
  /** Start a request; aborts any in-flight one. `fn` must pass the signal to fetch. */
  run<T>(fn: (signal: AbortSignal) => Promise<T>): Promise<GateOutcome<T>>;
  /** Abort the in-flight request, if any (path change, unmount). Its outcome is "ignored". */
  cancel(): void;
  /** True while the newest request has not settled. Lets background polls skip instead of piling up. */
  inFlight(): boolean;
};

export function createRequestGate(timeoutMs: number, timers: Timers = defaultTimers): RequestGate {
  let seq = 0;
  let current: { id: number; controller: AbortController; timer: unknown } | null = null;

  const stop = (kind: AbortKind) => {
    if (!current) return;
    timers.clear(current.timer);
    if (!current.controller.signal.aborted) current.controller.abort(new RequestAborted(kind));
    current = null;
  };

  return {
    async run<T>(fn: (signal: AbortSignal) => Promise<T>): Promise<GateOutcome<T>> {
      stop("superseded");
      const id = ++seq;
      const controller = new AbortController();
      const timer = timers.set(() => {
        if (current?.id === id) controller.abort(new RequestAborted("timeout"));
      }, timeoutMs);
      current = { id, controller, timer };
      const kind = (): AbortKind | null => {
        const r = controller.signal.reason;
        return controller.signal.aborted ? (r instanceof RequestAborted ? r.kind : "cancelled") : null;
      };
      try {
        const value = await fn(controller.signal);
        // A request that ignored its signal can still resolve late: only the newest, un-aborted one counts.
        const k = kind();
        if (id !== seq || (k !== null && k !== "timeout")) return { status: "ignored" };
        if (k === "timeout") return { status: "timeout" };
        return { status: "ok", value };
      } catch (error) {
        const k = kind();
        if (id !== seq || (k !== null && k !== "timeout")) return { status: "ignored" };
        if (k === "timeout") return { status: "timeout" };
        return { status: "error", error };
      } finally {
        if (current?.id === id) {
          timers.clear(current.timer);
          current = null;
        }
      }
    },
    cancel() {
      stop("cancelled");
    },
    inFlight() {
      return current !== null;
    },
  };
}
