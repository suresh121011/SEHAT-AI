// Run: node --test src/lib/aiCorrection.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildCorrection, correctionKindOf, initialCorrection, symptomWords } from "./aiCorrection.ts";

test("only the kinds the backend can correct get a form", () => {
  for (const k of ["measurement", "text", "medication", "symptom", "red_flag"]) assert.equal(correctionKindOf(k), k);
  assert.equal(correctionKindOf("ocr_lab"), null);
  assert.equal(correctionKindOf(""), null);
});

test("a measurement correction keeps the exact number and never rounds", () => {
  const r = buildCorrection("measurement", { value: "101.45", value2: "", unit: "F", negated: "" });
  assert.deepEqual(r, { ok: true, corrected: { value: 101.45, unit: "F" } });
  const bp = buildCorrection("measurement", { value: "130", value2: "85", unit: "mmHg", negated: "" });
  assert.deepEqual(bp, { ok: true, corrected: { value: 130, value2: 85, unit: "mmHg" } });
});

test("a measurement needs a real number; words, blanks and long units are refused", () => {
  for (const value of ["", "one hundred", "101,4", "38.5C"]) {
    const r = buildCorrection("measurement", { value, value2: "", unit: "", negated: "" });
    assert.equal(r.ok, false, value);
    if (!r.ok) assert.ok(r.errors.value);
  }
  const r = buildCorrection("measurement", { value: "120", value2: "x", unit: "a".repeat(21), negated: "" });
  assert.equal(r.ok, false);
  if (!r.ok) assert.ok(r.errors.value2 && r.errors.unit);
});

test("text and medication corrections are trimmed and bounded", () => {
  assert.deepEqual(buildCorrection("text", { value: "  fever since 4 days ", value2: "", unit: "", negated: "" }), { ok: true, corrected: { value: "fever since 4 days" } });
  assert.deepEqual(buildCorrection("medication", { value: "paracetamol 650 mg", value2: "", unit: "", negated: "" }), { ok: true, corrected: { value: "paracetamol 650 mg" } });
  assert.equal(buildCorrection("text", { value: "   ", value2: "", unit: "", negated: "" }).ok, false);
  assert.equal(buildCorrection("medication", { value: "x".repeat(201), value2: "", unit: "", negated: "" }).ok, false);
});

test("a symptom or red flag correction must say present or denied; nothing is assumed", () => {
  assert.equal(buildCorrection("symptom", { value: "", value2: "", unit: "", negated: "" }).ok, false);
  assert.deepEqual(buildCorrection("red_flag", { value: "", value2: "", unit: "", negated: "present" }), { ok: true, corrected: { negated: false } });
  assert.deepEqual(buildCorrection("symptom", { value: "", value2: "", unit: "", negated: "denied" }), { ok: true, corrected: { negated: true } });
});

test("the form starts from the extracted value", () => {
  assert.deepEqual(initialCorrection("measurement", { value: 102, unit: "F" }), { value: "102", value2: "", unit: "F", negated: "" });
  assert.deepEqual(initialCorrection("medication", { name: "paracetamol", dose: "500 mg" }).value, "paracetamol");
  assert.equal(initialCorrection("symptom", { name: "chest pain", negated: true }).negated, "denied");
  assert.equal(initialCorrection("measurement", null).value, "", "a disputed value (null) starts empty");
});

// ── per-symptom duration (docs/plans/clinical-note-v2-plan.md step 1) ────────────────────────────────────────────

test("a symptom is shown with its term, the words said and its own duration", () => {
  assert.equal(symptomWords({ name: "head is paining", term: "headache", negated: false, duration: { text: "2 hours", amount: 2, unit: "hour" } }), "headache (said: head is paining), 2 hours");
  assert.equal(symptomWords({ name: "fever", negated: false, duration: { text: "1 day", amount: 1, unit: "day" } }), "fever, 1 day");
  assert.equal(symptomWords({ name: "bukhar", term: "fever", negated: false, duration: { text: "do din", amount: null, unit: null } }), "fever (said: bukhar), do din (as said)");
  assert.equal(symptomWords({ name: "fever", negated: true, duration: { text: "2 days", amount: 2, unit: "day" } }), "denies fever"); // a denied symptom shows no duration
  assert.equal(symptomWords({ name: "cough", negated: false }), "cough");
});

test("a duration correction needs both the number and the unit, and is never sent for a denied symptom", () => {
  const base = initialCorrection("symptom", { name: "fever", negated: false, duration: { text: "2 days", amount: 2, unit: "day" } });
  assert.equal(base.durationAmount, "2");
  assert.equal(base.durationUnit, "day");
  assert.deepEqual(buildCorrection("symptom", { ...base, durationAmount: "5" }), { ok: true, corrected: { negated: false, duration_amount: 5, duration_unit: "day" } });
  assert.equal(buildCorrection("symptom", { ...base, durationAmount: "5", durationUnit: "" }).ok, false);
  assert.equal(buildCorrection("symptom", { ...base, durationAmount: "two" }).ok, false);
  assert.equal(buildCorrection("symptom", { ...base, durationAmount: "0" }).ok, false);
  assert.deepEqual(buildCorrection("symptom", { ...base, durationAmount: "", durationUnit: "" }), { ok: true, corrected: { negated: false } });
  assert.deepEqual(buildCorrection("symptom", { ...base, negated: "denied" }), { ok: true, corrected: { negated: true } });
  assert.deepEqual(buildCorrection("red_flag", { ...base, durationAmount: "5" }), { ok: true, corrected: { negated: false } }); // red flags carry no duration
});
