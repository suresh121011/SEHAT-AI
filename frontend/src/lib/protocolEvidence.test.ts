// Run: node --test src/lib/protocolEvidence.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { STATE_WORDS, idsParam, sourceHref } from "./protocolEvidence.ts";

test("the ids parameter keeps valid rule ids once, at most 50", () => {
  assert.equal(idsParam(["ATP_RED_RR", "NEWS2_LOW_MEDIUM", "ATP_RED_RR"]), "ATP_RED_RR,NEWS2_LOW_MEDIUM");
  assert.equal(idsParam(["atp red", "ATP_RED_RR;DROP", "OK_ID"]), "OK_ID");
  assert.equal(idsParam(Array.from({ length: 60 }, (_, i) => `RULE_${i}`)).split(",").length, 50);
  assert.equal(idsParam([]), "");
});

test("every gate state has words", () => {
  for (const s of ["VERIFIED", "UNCHECKED", "LOCATOR_ONLY", "WITHHELD"] as const) assert.ok(STATE_WORDS[s]);
});

test("sources link only to web addresses and DOIs", () => {
  assert.equal(sourceHref("doi:10.4103/jets.jets_146_21 (PMC9639733)"), "https://doi.org/10.4103/jets.jets_146_21");
  assert.equal(sourceHref("https://www.rcp.ac.uk/x"), "https://www.rcp.ac.uk/x");
  assert.equal(sourceHref("docs/10_Safety_Rules_Engine.md"), null);
  assert.equal(sourceHref("javascript:alert(1)"), null);
});
