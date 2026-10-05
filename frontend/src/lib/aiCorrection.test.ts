// Run: node --test src/lib/aiCorrection.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildCorrection, correctionKindOf, initialCorrection } from "./aiCorrection.ts";

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
