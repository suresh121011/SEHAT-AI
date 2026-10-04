// Run: node --test src/lib/requestGate.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { RequestAborted, createRequestGate } from "./requestGate.ts";

/** Manual timers: nothing fires until the test says so. */
function fakeTimers() {
  const pending = new Map<number, () => void>();
  let n = 0;
  return {
    timers: {
      set: (fn: () => void) => {
        pending.set(++n, fn);
        return n;
      },
      clear: (h: unknown) => void pending.delete(h as number),
    },
    fireAll: () => {
      for (const [k, fn] of [...pending]) {
        pending.delete(k);
        fn();
      }
    },
    count: () => pending.size,
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}

/** A fetch stand-in that rejects with the signal's reason when aborted, like the browser's fetch. */
function abortable<T>(signal: AbortSignal, d: { promise: Promise<T> }): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    d.promise.then(resolve, reject);
  });
}

test("latest wins: an older response arriving after a newer one is ignored", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  const a = deferred<string>();
  const b = deferred<string>();
  // Both requests ignore their signal, so the old one really does resolve late.
  const pa = gate.run(() => a.promise);
  const pb = gate.run(() => b.promise);
  b.resolve("new");
  a.resolve("old");
  assert.deepEqual(await pb, { status: "ok", value: "new" });
  assert.deepEqual(await pa, { status: "ignored" });
  assert.equal(gate.inFlight(), false);
  assert.equal(t.count(), 0, "timers are cleared");
});

test("a newer request aborts the one it supersedes", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  let firstSignal: AbortSignal | null = null;
  const a = deferred<string>();
  const pa = gate.run((s) => {
    firstSignal = s;
    return abortable(s, a);
  });
  const pb = gate.run(async () => "fresh");
  assert.equal(firstSignal!.aborted, true);
  assert.ok(firstSignal!.reason instanceof RequestAborted);
  assert.equal((firstSignal!.reason as RequestAborted).kind, "superseded");
  assert.deepEqual(await pa, { status: "ignored" });
  assert.deepEqual(await pb, { status: "ok", value: "fresh" });
});

test("timeout aborts the request and reports timeout (not a generic error)", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  const a = deferred<string>();
  let sig: AbortSignal | null = null;
  const pa = gate.run((s) => {
    sig = s;
    return abortable(s, a);
  });
  assert.equal(gate.inFlight(), true);
  t.fireAll();
  assert.deepEqual(await pa, { status: "timeout" });
  assert.equal((sig!.reason as RequestAborted).kind, "timeout");
  assert.equal(gate.inFlight(), false);
});

test("a late answer after timeout is not applied", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  const a = deferred<string>();
  const pa = gate.run(() => a.promise); // ignores its signal
  t.fireAll();
  a.resolve("too late");
  assert.deepEqual(await pa, { status: "timeout" });
});

test("cancel (path change / unmount) drops the in-flight answer silently", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  const a = deferred<string>();
  const pa = gate.run((s) => abortable(s, a));
  gate.cancel();
  assert.deepEqual(await pa, { status: "ignored" });
  const b = deferred<string>();
  const pb = gate.run(() => b.promise); // ignores signal, resolves after cancel
  gate.cancel();
  b.resolve("x");
  assert.deepEqual(await pb, { status: "ignored" });
  assert.equal(t.count(), 0);
});

test("errors from the newest request are reported; errors from superseded ones are not", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  const a = deferred<string>();
  const pa = gate.run(() => a.promise);
  const pb = gate.run(async () => {
    throw new Error("boom");
  });
  a.reject(new Error("old boom"));
  const rb = await pb;
  assert.equal(rb.status, "error");
  assert.equal((rb as { error: Error }).error.message, "boom");
  assert.deepEqual(await pa, { status: "ignored" });
});

test("the gate never retries on its own", async () => {
  const t = fakeTimers();
  const gate = createRequestGate(20_000, t.timers);
  let calls = 0;
  const r = await gate.run(async () => {
    calls++;
    throw new Error("down");
  });
  assert.equal(r.status, "error");
  assert.equal(calls, 1);
});
