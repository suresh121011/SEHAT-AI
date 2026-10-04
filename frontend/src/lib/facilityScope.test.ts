// Run: node --test src/lib/facilityScope.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { facilityNotice } from "./facilityScope.ts";

test("isolation off: plain warning, never claims a privacy wall", () => {
  const n = facilityNotice({ role: "medical_officer", facility_scope: null, facility_isolation: "off" });
  assert.equal(n.kind, "open");
  assert.match(n.text, /every clinic's patients to every account/);
  assert.match(n.text, /No facility privacy wall is active/);
});

test("isolation enforced: lists the server's scope in order", () => {
  const n = facilityNotice({ role: "medical_officer", facility_scope: ["PHC-A", "PHC-B"], facility_isolation: "enforced" });
  assert.equal(n.kind, "scoped");
  assert.equal(n.text, "Patient lists are limited to: PHC-A, PHC-B (set per demo account by the server).");
});

test("wildcard scope reads as all facilities", () => {
  const n = facilityNotice({ role: "supervisor", facility_scope: ["*"], facility_isolation: "enforced" });
  assert.equal(n.text, "This account can see all facilities (set by the server).");
});

test("missing fields (older server) or no session: unknown, not assumed safe", () => {
  for (const me of [null, { role: "medical_officer" }, { role: "x", facility_isolation: "maybe" }]) {
    const n = facilityNotice(me);
    assert.equal(n.kind, "unknown");
    assert.match(n.text, /Do not assume/);
  }
});

test("enforced with an empty scope says so instead of inventing one", () => {
  assert.match(facilityNotice({ role: "medical_officer", facility_scope: [], facility_isolation: "enforced" }).text, /no facility/);
});

test("enforced isolation with a null scope means all facilities, never 'no facility' (Agent D walkthrough defect)", () => {
  const n = facilityNotice({ role: "supervisor", facility_scope: null, facility_isolation: "enforced" });
  assert.equal(n.kind, "scoped");
  assert.match(n.text, /all facilities/);
  assert.doesNotMatch(n.text, /no facility/);
});
