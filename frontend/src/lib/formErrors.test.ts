// Run: node --test src/lib/formErrors.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildSummary, failureKind, fieldMessage, mapServerErrors, serverFieldErrors, tableResolver } from "./formErrors.ts";

const resolve = tableResolver({ reason_code: "reason_code", reason_text: "reason_text", "input.vitals.spo2": "spo2", new_urgency: "new_urgency" });
const label = (k: string) => ({ reason_code: "Reason code", reason_text: "Explanation", spo2: "SpO2", new_urgency: "Urgency" })[k] ?? k;

test("serverFieldErrors reads details.errors and drops malformed entries", () => {
  assert.deepEqual(serverFieldErrors({ errors: [{ field: "reason_code", constraint: "Value not allowed" }, { nope: 1 }, null, "x", { field: 3 }] }), [{ field: "reason_code", constraint: "Value not allowed" }]);
  assert.deepEqual(serverFieldErrors({}), []);
  assert.deepEqual(serverFieldErrors(undefined), []);
  assert.deepEqual(serverFieldErrors({ errors: "bad" }), []);
});

test("mapServerErrors puts known paths next to their control and keeps unknown ones for the summary", () => {
  const m = mapServerErrors(
    [
      { field: "reason_text", constraint: "Too short" },
      { field: "input.vitals.spo2", constraint: "Value out of allowed range" },
      { field: "input.vitals", constraint: "Invalid value" },
      { field: "", constraint: "Value error" },
      { field: "<extra>", constraint: "Unexpected field" },
    ],
    resolve,
  );
  assert.deepEqual(m.fields, { reason_text: "Too short", spo2: "Value out of allowed range" });
  assert.equal(m.unmapped.length, 3);
  assert.match(m.unmapped[0], /input\.vitals: Invalid value/);
  assert.match(m.unmapped[1], /request as a whole: Value error/);
});

test("mapServerErrors merges several messages for one control without duplicates", () => {
  const m = mapServerErrors(
    [
      { field: "reason_text", constraint: "Too short" },
      { field: "reason_text", constraint: "Too short" },
      { field: "reason_text", constraint: "Invalid format" },
      { field: "reason_code" },
    ],
    resolve,
  );
  assert.equal(m.fields.reason_text, "Too short; Invalid format");
  assert.equal(m.fields.reason_code, "Invalid value");
});

test("buildSummary orders by control order, client first, then unmapped", () => {
  const server = mapServerErrors([{ field: "", constraint: "Value error" }, { field: "input.vitals.spo2", constraint: "Value out of allowed range" }, { field: "reason_code", constraint: "Value not allowed" }], resolve);
  const items = buildSummary(["reason_code", "reason_text", "spo2"], { reason_code: "Choose a reason code." }, server, label);
  assert.deepEqual(items, [
    { key: "reason_code", message: "Reason code: Choose a reason code." },
    { key: "spo2", message: "SpO2: Value out of allowed range" },
    { key: null, message: "The request as a whole: Value error" },
  ]);
  assert.equal(fieldMessage("reason_code", { reason_code: "client" }, server), "client");
  assert.equal(fieldMessage("spo2", {}, server), "Value out of allowed range");
  assert.equal(fieldMessage("reason_text", {}, null), undefined);
});

test("buildSummary includes controls missing from the order list", () => {
  const items = buildSummary([], { confirm: "Tick the confirmation." }, null, (k) => k);
  assert.deepEqual(items, [{ key: "confirm", message: "confirm: Tick the confirmation." }]);
});

test("failureKind separates validation, authorization, conflict, PII, server and network", () => {
  assert.equal(failureKind(400, "VALIDATION_ERROR"), "validation");
  assert.equal(failureKind(403, "CONSENT_REQUIRED"), "authorization");
  assert.equal(failureKind(403, "FORBIDDEN"), "authorization");
  assert.equal(failureKind(401, "UNAUTHENTICATED"), "session");
  assert.equal(failureKind(409, "STALE_TRIAGE_RUN"), "conflict");
  assert.equal(failureKind(422, "PII_DETECTED"), "pii");
  assert.equal(failureKind(404, "NOT_FOUND"), "not_found");
  assert.equal(failureKind(500, "INTERNAL"), "server");
  assert.equal(failureKind(502, "HTTP_ERROR"), "server");
  assert.equal(failureKind(0), "network");
  assert.equal(failureKind(undefined), "network");
});
