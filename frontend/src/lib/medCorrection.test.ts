// Run: node --test src/lib/medCorrection.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { STRENGTH_UNCLEAR, buildMedCorrection, medLineText } from "./medCorrection.ts";

const acyclovir = { drug_raw: "Acyclovir", strength_raw: "500mg", frequency_raw: "5 Times a day", duration_raw: "7 DAYS" };

test("a strength the engines disagree on is never shown as the dose", () => {
  const text = medLineText({ ...acyclovir, strength_status: "readings_differ", disputed_parts: ["strength"] });
  assert.ok(!text.includes("500"));
  assert.ok(text.includes(STRENGTH_UNCLEAR));
  assert.ok(text.includes("5 Times a day") && text.includes("7 DAYS"));
});

test("an agreed strength is shown as read", () => {
  assert.equal(medLineText({ ...acyclovir, strength_status: "read" }), "500mg · 5 Times a day · 7 DAYS");
  assert.equal(medLineText({ line_raw: "illegible line" }), "illegible line");
});

const empty = { strength: "", pattern: "", frequency: "", durationNumber: "", durationUnit: "days" };

test("the reviewer's typed 800 mg becomes a closed-format correction", () => {
  assert.deepEqual(buildMedCorrection({ ...empty, strength: "800 MG" }, true), { correction: { strength_raw: "800 mg" } });
  assert.deepEqual(buildMedCorrection({ ...empty, strength: "800mg", frequency: "BD", durationNumber: "7" }, true),
    { correction: { strength_raw: "800mg", frequency_raw: "BD", duration_raw: "7 days" } });
  assert.deepEqual(buildMedCorrection({ ...empty, durationNumber: "1", durationUnit: "weeks" }, false), { correction: { duration_raw: "1 week" } });
});

test("a disputed strength must be typed; free text and bad shapes are refused", () => {
  assert.ok("error" in buildMedCorrection({ ...empty, frequency: "BD" }, true));
  assert.ok("error" in buildMedCorrection({ ...empty, strength: "eight hundred" }, false));
  assert.ok("error" in buildMedCorrection({ ...empty, pattern: "morning" }, false));
  assert.ok("error" in buildMedCorrection({ ...empty, frequency: "twice daily" }, false));
  assert.ok("error" in buildMedCorrection(empty, false));
});
