// Run: node --test src/lib/evidence.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { fieldAt, instruction, regionStyle, toDisplay, validBox, type Region } from "./evidence.ts";

const page = { width: 1240, height: 1754 };

test("percent geometry is resize-invariant", () => {
  const s = regionStyle([520, 400, 620, 440], page)!;
  assert.equal(s.left, "41.9355%");
  assert.equal(s.width, "8.0645%");
  for (const w of [320, 768, 1280]) {
    const [x0, , x1] = toDisplay([520, 400, 620, 440], page, w);
    assert.ok(Math.abs(x0 / w - 520 / 1240) < 1e-9 && Math.abs((x1 - x0) / w - 100 / 1240) < 1e-9);
  }
});

test("missing, empty or out-of-page boxes are never drawn", () => {
  assert.equal(regionStyle(null, page), null);
  assert.equal(regionStyle([10, 10, 10, 20], page), null);
  assert.equal(regionStyle([0, 0, 1300, 20], page), null);
  assert.equal(regionStyle([1, 2, 3], page), null);
  assert.equal(validBox([-1, 0, 10, 10], page), false);
});

const regions = (bbox: [number, number, number, number], page_index = 0): Region[] => [{ role: "value", page_index, bbox }];
const fields = [
  { id: "hb", regions: regions([520, 386, 580, 420]) },
  { id: "tlc", regions: regions([520, 432, 600, 466]) }, // adjacent row
  { id: "row", regions: regions([80, 380, 1160, 470]) }, // a large overlapping box
  { id: "p2", regions: regions([520, 386, 580, 420], 1) },
];

test("hit-test selects the right field at phone, tablet and desktop widths", () => {
  for (const w of [320, 768, 1280]) {
    const s = w / page.width;
    assert.equal(fieldAt(fields, 0, page, w, 550 * s, 400 * s), "hb");
    assert.equal(fieldAt(fields, 0, page, w, 550 * s, 450 * s), "tlc");
    assert.equal(fieldAt(fields, 0, page, w, 900 * s, 400 * s), "row"); // only the big box there
    assert.equal(fieldAt(fields, 0, page, w, 10 * s, 10 * s), null);
  }
});

test("other pages' boxes are ignored", () => {
  assert.equal(fieldAt([fields[3]], 0, page, 1240, 550, 400), null);
  assert.equal(fieldAt([fields[3]], 1, page, 1240, 550, 400), "p2");
});

test("instructions never claim verification", () => {
  const base = { band: "accept", disputed: false, can_confirm: true, checks: [] };
  for (const f of [base, { ...base, band: "amber" }, { ...base, disputed: true }, { ...base, band: "human_entry" }]) {
    assert.ok(!/verif/i.test(instruction(f).text));
  }
  assert.match(instruction({ ...base, disputed: true }).text, /Two different readings/);
  assert.notEqual(instruction(base).symbol, "✓"); // the confirm button's tick is never shown on an unreviewed row
  // A decided row no longer says "ready to check" (pre-Phase 9 walkthrough); an unreviewed one still does.
  for (const review_status of ["confirmed", "corrected", "unsure", "rejected"]) assert.match(instruction({ ...base, review_status }).text, /Decision recorded/);
  assert.doesNotMatch(instruction({ ...base, review_status: "machine_read" }).text, /Decision recorded/);
});
