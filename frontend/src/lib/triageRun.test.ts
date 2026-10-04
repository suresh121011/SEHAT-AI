// Run: node --test src/lib/triageRun.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { STALE_RUN_MESSAGE, expectedRunId, isStaleRunConflict, triagePath } from "./triageRun.ts";

test("first run sends 'none'", () => {
  assert.equal(expectedRunId(null, null), "none");
  assert.equal(expectedRunId(undefined, undefined), "none");
});

test("case's latest run is used when this screen has not submitted", () => {
  assert.equal(expectedRunId("run-1", null), "run-1");
});

test("this screen's own last successful run wins for a later resubmit", () => {
  assert.equal(expectedRunId("run-1", "run-2"), "run-2");
  assert.equal(expectedRunId(null, "run-2"), "run-2");
});

test("path encodes the case and expectation", () => {
  assert.equal(triagePath("abc", "none"), "cases/abc/triage?expected_run_id=none");
  assert.equal(triagePath("a/b", "x y"), "cases/a%2Fb/triage?expected_run_id=x%20y");
});

test("only the two 409 codes count as a stale-run conflict", () => {
  assert.equal(isStaleRunConflict(409, "STALE_TRIAGE_RUN"), true);
  assert.equal(isStaleRunConflict(409, "EXPECTED_RUN_REQUIRED"), true);
  assert.equal(isStaleRunConflict(409, "ALREADY_ACKNOWLEDGED"), false);
  assert.equal(isStaleRunConflict(400, "STALE_TRIAGE_RUN"), false);
});

test("message says nothing was saved and never promises an automatic retry", () => {
  assert.match(STALE_RUN_MESSAGE, /Nothing was saved/);
  assert.doesNotMatch(STALE_RUN_MESSAGE, /automatic/i);
});
