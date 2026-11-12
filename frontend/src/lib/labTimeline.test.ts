// Run: node --test src/lib/labTimeline.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { DATE_SOURCE_WORDS, OUTCOME_WORDS, dateLine, resultLine, showDate, visitWords, visitsLine, type TimelineResult } from "./labTimeline.ts";

const RESULT: TimelineResult = {
  date: "2026-10-12", date_source: "report_date", date_machine_read: true, date_regions: [], value: "11.2", comparator: null, qualitative: null, unit: "g/dl", shown: "11.2 g/dl",
  outcome: "confirmed", flags: [{ code: "below_printed_range", text: "Below the range printed on the report" }],
  change: { direction: "higher", text: "Higher than the previous result (9.8 g/dl)" },
  case_id: "c2", visit_date: "2026-10-12", current_visit: true, source: { type: "ocr", document_id: "d2", field_id: "f2", page_index: 0 },
};

test("dates read as day, month name and year; anything unexpected is shown as given", () => {
  assert.equal(showDate("2026-10-12"), "12 Oct 2026");
  assert.equal(showDate("2026-01-05"), "5 Jan 2026");
  assert.equal(showDate("12/10/2026"), "12/10/2026");
  assert.equal(showDate("2026-13-01"), "2026-13-01");
});

test("every date source and outcome the server sends has words", () => {
  for (const k of ["report_date", "collected_date", "visit_date"] as const) assert.ok(DATE_SOURCE_WORDS[k]);
  for (const k of ["confirmed", "corrected"] as const) assert.ok(OUTCOME_WORDS[k]);
  assert.equal(dateLine({ date: "2026-10-12", date_source: "visit_date", date_machine_read: false }), "12 Oct 2026 (visit date (no readable date on the report))");
  assert.equal(dateLine({ date: "2026-10-12", date_source: "report_date", date_machine_read: true }), "12 Oct 2026 (report date, read by the machine, not checked)");
});

test("a result line keeps the value, how it was checked, its flags and the comparison", () => {
  assert.equal(
    resultLine(RESULT),
    "11.2 g/dl · machine reading confirmed by a person · Below the range printed on the report · Higher than the previous result (9.8 g/dl)",
  );
  assert.equal(resultLine({ ...RESULT, flags: [], change: null, outcome: "corrected" }), "11.2 g/dl · entered from the paper by a person");
});

test("visits are this or other, never assumed earlier, and the count includes this visit", () => {
  assert.equal(visitWords(true), "this visit");
  assert.equal(visitWords(false), "other visit");
  assert.equal(visitsLine(1), "Only this visit is included.");
  assert.equal(visitsLine(2), "This visit and 1 linked visit are included.");
  assert.equal(visitsLine(3), "This visit and 2 linked visits are included.");
});
