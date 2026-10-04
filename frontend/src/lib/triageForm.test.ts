import assert from "node:assert/strict";
import { test } from "node:test";

import { buildTriageInput, emptyForm, fieldForErrorPath, mergePrefill, type FormState } from "./triageForm.ts";

const base = (over: Partial<FormState> = {}): FormState => ({ ...emptyForm("opd"), ...over });

test("empty OPD form builds a minimal strict payload with no invented values", () => {
  const r = buildTriageInput(base());
  assert.ok(r.ok);
  if (!r.ok) return;
  assert.deepEqual(r.input, { scenario: "opd", pregnant: false, vitals: {}, red_flag_screen_completed: false, red_flags_present: [], suspected_infection: false });
});

test("numbers are sent as real ints/floats; temperature is never rounded", () => {
  const r = buildTriageInput(base({ age_years: "34", spo2: "91", pulse: "112", sbp: "150", dbp: "90", resp_rate: "24", temp_c: "39.04", consciousness: "A", on_supplemental_oxygen: "no" }));
  assert.ok(r.ok);
  if (!r.ok) return;
  assert.equal(r.input.age_years, 34);
  assert.deepEqual(r.input.vitals, { resp_rate: 24, spo2: 91, pulse: 112, sbp: 150, dbp: 90, temp_c: 39.04, on_supplemental_oxygen: false, consciousness: "A" });
});

test("backend ranges and cross-field rules are enforced before sending", () => {
  const r = buildTriageInput(base({ spo2: "101", pulse: "9.5", sbp: "90", dbp: "95", temp_c: "102", age_years: "-1" }));
  assert.equal(r.ok, false);
  if (r.ok) return;
  assert.match(r.errors.spo2!, /between 0 and 100/);
  assert.match(r.errors.pulse!, /whole number/);
  assert.match(r.errors.dbp!, /lower than systolic/);
  assert.match(r.errors.temp_c!, /°C/);
  assert.match(r.errors.age_years!, /whole number/);
});

test("maternal requires pregnant; trimester requires pregnant", () => {
  const m = buildTriageInput({ ...emptyForm("maternal"), pregnant: false });
  assert.equal(m.ok, false);
  const t = buildTriageInput(base({ trimester: "2" }));
  assert.equal(t.ok, false);
  const ok = buildTriageInput({ ...emptyForm("maternal"), trimester: "3" });
  assert.ok(ok.ok && ok.input.trimester === 3 && ok.input.pregnant === true);
});

test("red flags need an explicit screen confirmation and must be known AtpFlag values", () => {
  assert.equal(buildTriageInput(base({ red_flags_present: ["chest_pain_acute_24h"] })).ok, false);
  assert.equal(buildTriageInput(base({ red_flags_present: ["made_up"], red_flag_screen_completed: true })).ok, false);
  const ok = buildTriageInput(base({ red_flags_present: ["chest_pain_acute_24h"], red_flag_screen_completed: true }));
  assert.ok(ok.ok && ok.input.red_flags_present[0] === "chest_pain_acute_24h");
});

test("the red-flag screen is never pre-ticked", () => {
  assert.equal(emptyForm("opd").red_flag_screen_completed, false);
  assert.deepEqual(emptyForm("opd").red_flags_present, []);
});

test("backend validation paths map back to form fields", () => {
  assert.equal(fieldForErrorPath("vitals.spo2"), "spo2");
  assert.equal(fieldForErrorPath("body.vitals.temp_c"), "temp_c");
  assert.equal(fieldForErrorPath("red_flags_present.0"), "red_flags_present");
  assert.equal(fieldForErrorPath(""), null);
});

test("prefill uses only human-checked values, keeps sources, and blanks conflicts", () => {
  const voice = {
    vitals: { spo2: 91, temp_c: 38.9 },
    fields: { age_years: 34 },
    values: { spo2: { values: { spo2: 91 }, sources: [{ type: "voice_transcript", resolved_by_role: "anm" }] }, temp: { values: { temp_c: 38.9 }, sources: [{ type: "voice_manual_correction", resolved_by_role: "anm" }] } },
  };
  const ai = { values: [{ field: "spo2", basis: "ai_extracted_accepted_by_reviewer", form_hints: [{ form_field: "vitals.spo2", value: 93 }] }, { field: "pulse", basis: "reviewer_corrected", form_hints: [{ form_field: "vitals.pulse", value: 110 }] }, { field: "red_flag:x", basis: "ai_extracted_accepted_by_reviewer", form_hints: [{ form_field: "red_flags_present", value: "chest_pain_acute_24h" }] }] };
  const p = mergePrefill(voice, ai);
  assert.deepEqual(p.conflicts, ["spo2"]);
  assert.equal(p.values.spo2, undefined); // 91 (voice) vs 93 (AI): left blank for a person
  assert.equal(p.values.temp_c, "38.9");
  assert.equal(p.sources.temp_c![0].kind, "voice_corrected");
  assert.equal(p.values.pulse, "110");
  assert.equal(p.sources.pulse![0].kind, "ai_corrected");
  assert.equal(p.values.age_years, "34");
  assert.ok(!Object.keys(p.values).includes("red_flags_present")); // red flags are never pre-filled (also enforced by the PrefillKey type)
});

test("a disputed blood pressure half blanks the whole reading", () => {
  const voice = { vitals: { sbp: 140, dbp: 90 }, fields: {}, values: {} };
  const ai = { values: [{ field: "bp", basis: "ai_extracted_accepted_by_reviewer", form_hints: [{ form_field: "vitals.sbp", value: 120 }] }] };
  const p = mergePrefill(voice, ai);
  assert.equal(p.values.sbp, undefined);
  assert.equal(p.values.dbp, undefined);
  assert.deepEqual([...p.conflicts].sort(), ["dbp", "sbp"]);
});
