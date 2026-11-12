// Run: node --test src/lib/referral.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { BLOCKED_WORDS, EMPTY_FORM, NEXT_STEP_WORDS, detailKind, fhirFileName, formErrors, sinceText, toRequest, vitalLine } from "./referral.ts";

const FILLED = { to_facility: "DH-BBSR-01", reason_code: "emergency_care", transport_mode: "108_ambulance", escort: "yes" as const, travel_minutes: "45", insurance_scheme: "pmjay" };

test("an empty form names every required choice and nothing optional", () => {
  const e = formErrors(EMPTY_FORM);
  assert.deepEqual(Object.keys(e).sort(), ["escort", "insurance_scheme", "reason_code", "to_facility", "transport_mode"]);
  assert.deepEqual(formErrors(FILLED), {});
  assert.deepEqual(formErrors({ ...FILLED, travel_minutes: "" }), {});
});

test("travel time is whole minutes from 1 to 1440", () => {
  for (const bad of ["0", "1441", "4.5", "-3", "abc", "1e2"]) assert.ok(formErrors({ ...FILLED, travel_minutes: bad }).travel_minutes, bad);
  for (const ok of ["1", " 45 ", "1440"]) assert.equal(formErrors({ ...FILLED, travel_minutes: ok }).travel_minutes, undefined, ok);
});

test("the request carries codes, a boolean escort and an optional number", () => {
  assert.deepEqual(toRequest(FILLED, "run-1"), {
    triage_run_id: "run-1", to_facility: "DH-BBSR-01", reason_code: "emergency_care", transport_mode: "108_ambulance",
    escort_required: true, travel_minutes: 45, insurance_scheme: "pmjay", confirm: true,
  });
  const r = toRequest({ ...FILLED, escort: "no", travel_minutes: "" }, "run-1");
  assert.equal(r.escort_required, false);
  assert.equal(r.travel_minutes, null);
});

test("closing steps need a code; the others do not", () => {
  assert.equal(detailKind("outcome_recorded"), "outcome");
  assert.equal(detailKind("not_reached"), "barrier");
  for (const s of ["referred", "in_transit", "reached", "seen"] as const) assert.equal(detailKind(s), null);
  assert.equal(Object.keys(NEXT_STEP_WORDS).length, 6);
});

test("every reason the server gives for not referring has words", () => {
  for (const code of ["no_triage_run", "sign_off_required", "referral_open", "consent", "role"]) assert.ok(BLOCKED_WORDS[code], code);
});

test("time since the last update, and overdue wording", () => {
  assert.equal(sinceText(0, false, 48), "Updated within the last hour");
  assert.equal(sinceText(5, false, 48), "5 h since the last update");
  assert.equal(sinceText(52, true, 48), "Overdue: no update for 52 h (limit 48 h)");
});

test("letter lines and the export file name", () => {
  assert.equal(vitalLine({ field: "spo2", label: "SpO2", value: 98, unit: "%" }), "SpO2: 98 %");
  assert.equal(vitalLine({ field: "consciousness", label: "Consciousness (ACVPU)", value: "A", unit: null }), "Consciousness (ACVPU): A");
  assert.equal(fhirFileName({ patient_token: "PT 1/2", referral_id: "1234567890ab" }), "referral-PT-1-2-12345678.fhir.json");
});
