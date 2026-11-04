// Run: node --test src/lib/verificationQueue.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { PRIORITY_WORDS, keyAction, nextDocumentField, reasonWords, urgencyWords, type QueueItem } from "./verificationQueue.ts";

const field = (id: string, doc = "d1", priority: QueueItem["priority"] = "routine"): QueueItem => ({
  item_id: `document_field:${id}`, kind: "document_field", priority, reasons: [], label: id, shown: "1", ref: { document_id: doc, field_id: id },
  act_on: "documents", can_confirm: true, urgency_if_confirmed: null,
});

test("every priority has plain words, and the urgency one says what a person should do", () => {
  for (const p of ["match_document", "could_change_urgency", "abnormal_lab", "routine"] as const) assert.ok(PRIORITY_WORDS[p].title.length > 0);
  assert.match(PRIORITY_WORDS.could_change_urgency.title, /could make this patient urgent/i);
});

test("reasons are shown as words; an unknown reason code is still shown, never dropped", () => {
  assert.deepEqual(reasonWords(["printed_flag_low", "readings_differ"]), ["printed L (low) on the report", "the two readings differ"]);
  assert.deepEqual(reasonWords(["something_new"]), ["something new"]);
});

test("the hypothetical urgency is labelled as hypothetical and never shown when the engine gave none", () => {
  assert.equal(urgencyWords(null), null);
  const w = urgencyWords({ now: "YELLOW", if_confirmed: "RED", hypothetical: true });
  assert.ok(w && w.includes("YELLOW") && w.includes("RED") && /if this value is right/i.test(w) && /not recorded/i.test(w));
  assert.equal(urgencyWords({ now: "GREEN", if_confirmed: "GREEN", hypothetical: true }), null);
});

test("after a decision the next row is the highest-priority document row that is not the one just decided", () => {
  const items: QueueItem[] = [
    { ...field("x"), kind: "document_attestation", ref: { document_id: "d2" } },
    field("a", "d1", "abnormal_lab"), field("b", "d2", "routine"),
  ];
  assert.deepEqual(nextDocumentField(items, "a"), { doc: "d2", field: "b" });
  assert.deepEqual(nextDocumentField(items, "zzz"), { doc: "d1", field: "a" });
  assert.equal(nextDocumentField([items[0]], "a"), null);
});

test("keyboard: C confirms only when allowed; U and R map to not sure and reject", () => {
  assert.equal(keyAction("c", "BUTTON", true), "confirmed");
  assert.equal(keyAction("C", "BODY", false), null);
  assert.equal(keyAction("u", "BODY", true), "unsure");
  assert.equal(keyAction("R", "DIV", true), "rejected");
  assert.equal(keyAction("x", "BODY", true), null);
});

test("keyboard: never Enter, never while typing in a box, never with Ctrl/Cmd/Alt (Cmd+R reloads)", () => {
  assert.equal(keyAction("Enter", "BODY", true), null);
  for (const tag of ["INPUT", "SELECT", "TEXTAREA"]) assert.equal(keyAction("r", tag, true), null);
  assert.equal(keyAction("r", "BODY", true, true), null);
});
