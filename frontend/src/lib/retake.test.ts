import assert from "node:assert/strict";
import { test } from "node:test";
import { retakeAdvice } from "./retake.ts";

test("text too small asks for a higher-resolution copy", () => {
  const [a] = retakeAdvice(["text_too_small"]);
  assert.match(a, /too small/);
  assert.match(a, /higher-resolution/);
});

test("every backend quality reason has specific advice; duplicates collapse; unknown reasons still explained", () => {
  for (const r of ["blurry", "skewed", "too_dark", "overexposed", "low_contrast", "blank_page", "text_too_small"]) {
    assert.doesNotMatch(retakeAdvice([r])[0], /could not be read \(/, r);
  }
  assert.equal(retakeAdvice(["blurry", "blurry", "too_dark"]).length, 2);
  assert.match(retakeAdvice(["new_reason"])[0], /new reason/);
  assert.equal(retakeAdvice([]).length, 1);
});
