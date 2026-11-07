// Run: node --test src/lib/patientCode.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { explainPatientError, formatTyped, looksComplete } from "./patientCode.ts";

test("typed codes are grouped like the slip, whatever the case or spaces; a pasted prefix is dropped", () => {
  assert.equal(formatTyped("4k7m9qxd"), "4K7M-9QXD");
  assert.equal(formatTyped("sp-4K7M 9QXD"), "4K7M-9QXD"); // whole pasted code
  assert.equal(formatTyped("4K7"), "4K7");
  assert.equal(formatTyped(""), "");
  assert.equal(formatTyped("4K7M9QXDEX"), "4K7M-9QXD"); // never more than 8 characters
});

test("a code that itself starts with S P is never mistaken for the prefix", () => {
  assert.equal(formatTyped("SPAB12CD"), "SPAB-12CD");
  assert.equal(looksComplete("SPAB12CD"), true);
});

test("a code is sent only when it has all 8 characters", () => {
  assert.equal(looksComplete("SP-4K7M-9QXD"), true); // whole pasted code
  assert.equal(looksComplete("4K7M-9QX"), false); // the box shows "SP-" outside it, so only the 8 characters are typed
});

test("every registry error has plain words; a typo is told apart from an unknown code", () => {
  const typo = explainPatientError({ status: 400, code: "PATIENT_CODE_INVALID", message: "x" });
  const unknown = explainPatientError({ status: 404, code: "NOT_FOUND", message: "x" });
  assert.match(typo, /mistyped/);
  assert.match(unknown, /No patient with this code in your facilities/);
  assert.notEqual(typo, unknown);
  assert.match(explainPatientError({ status: 403, code: "CONSENT_REQUIRED", message: "x" }), /consent step/);
  assert.match(explainPatientError(new Error("network")), /Nothing was recorded/);
});
