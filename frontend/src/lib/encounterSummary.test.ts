// Run: node --test src/lib/encounterSummary.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { acceptState, attentionTone, factsFor, flagWords, reviewWords, sortedAttention, sourceWords, TRUST_WORDS, type EncounterSummary, type SummaryView } from "./encounterSummary.ts";

const summary = (over: Partial<EncounterSummary> = {}): EncounterSummary => ({
  schema_version: "x", case_id: "c", scenario: "opd", status: "complete", generated_at: "t", attention: [], presenting_problem: [], timeline: [],
  symptoms: { present: [], denied: [] }, vitals: [], labs: [], medications: [], body_map: [], red_flag_mentions: [], context: [], missing_information: [],
  follow_up_questions: [], conflicts: [], pending_review: [], unavailable_sources: [], excluded_sources: [],
  urgency: { status: "not_determined", recorded_urgency: null, message: "" }, narrative: [], blocked: [], facts: {},
  trust_legend: { checked: "", entered: "", unchecked: "" }, disclaimer: "", ...over,
});

test("unsafe-to-rely-on items lead the attention list", () => {
  const items = ["pending_review", "triage_predates_evidence", "conflict", "unavailable_source", "blocked_sentence"].map((kind) => ({ kind, message: "", action: "", refs: [] }));
  assert.deepEqual(sortedAttention(items).map((a) => a.kind), ["unavailable_source", "conflict", "triage_predates_evidence", "blocked_sentence", "pending_review"]);
  assert.equal(attentionTone("conflict"), "danger");
  assert.equal(attentionTone("pending_review"), "attention");
});

test("accept is never offered on a stale, partial or missing summary; conflicts need acknowledgement", () => {
  assert.equal(acceptState({ case_id: "c", summary: null }).allowed, false);
  assert.equal(acceptState({ case_id: "c", summary: summary(), is_stale: true }).allowed, false);
  assert.equal(acceptState({ case_id: "c", summary: summary({ status: "partial" }) }).allowed, false);
  const ok = acceptState({ case_id: "c", summary: summary(), is_stale: false });
  assert.deepEqual(ok, { allowed: true, needsConflictAck: false, reason: null });
  const conflicted: SummaryView = { case_id: "c", is_stale: false, summary: summary({ conflicts: [{ key: "spo2", label: "SpO2", fact_ids: [], values: [], action: "" }] }) };
  assert.equal(acceptState(conflicted).needsConflictAck, true);
});

test("source words name the place and the checker, and never invent one", () => {
  assert.match(sourceWords({ type: "voice_transcript", transcript_chars: [10, 25], resolved_by_role: "anm" }), /characters 10–25 · checked by health worker/);
  assert.match(sourceWords({ type: "ocr", document_type: "prescription", page_index: 0 }), /^Prescription, page 1/);
  assert.match(sourceWords({ type: "ocr_manual_correction", document_type: "lab_report", page_index: 1 }), /Lab report, page 2, typed by the reviewer/);
  assert.match(sourceWords({ type: "ai_field", evidence: [{ quote: "fever for 3 days" }] }), /“fever for 3 days”/);
  assert.match(sourceWords({ type: "voice_transcript" }), /\(not recorded\)/);
  assert.equal(sourceWords({ type: "something_new" }), "Source recorded (type not shown)");
});

test("review words never call an accepted summary a diagnosis", () => {
  assert.match(reviewWords("accepted").text, /not a diagnosis/);
  assert.equal(reviewWords(undefined).tone, "neutral");
  assert.ok(!/verified|confirmed diagnosis/i.test(Object.values(TRUST_WORDS).map((t) => t.label).join(" ")));
});

test("facts are looked up by id and unknown ids are skipped, not fabricated", () => {
  const f = { fact_id: "F1", section: "vitals", key: "spo2", label: "SpO2", display: "97%", value: 97, provenance: "triage_form", trust: "entered" as const, sources: [{ type: "triage_form" }], flags: [] };
  assert.deepEqual(factsFor(summary({ facts: { F1: f } }), ["F1", "F2"]).map((x) => x.fact_id), ["F1"]);
});

test("uncertain handwriting is spelled out; internal flags are hidden", () => {
  assert.match(flagWords("uncertain_handwriting") ?? "", /do not act on this reading/);
  assert.equal(flagWords("band:amber"), null);
  assert.equal(flagWords("linked_to:Fabc"), null);
});
