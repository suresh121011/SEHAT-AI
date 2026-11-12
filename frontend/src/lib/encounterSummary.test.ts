// Run: node --test src/lib/encounterSummary.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  AGREEMENT_WORDS, READINESS_WORDS, acceptState, canOpenVerification, groupWithheld, verificationBase, attentionTone, documentLink, factsFor, flagWords, machineAgreed, ocrAgreement, readinessReasonWords,
  reviewWords, sortedAttention, sourceWords, TRUST_WORDS, verifyLink, withheldReasonWords, imageDescribedBy, imageFieldRows, openItems, openItemsWords, imageNeedsReview, imageValue, noteSections, shouldAutoBuild,
  type EncounterSummary, type Fact, type SummaryView,
} from "./encounterSummary.ts";

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

// ── H1-3: unmatched documents, machine agreement, withheld content, evidence status ───────────────────────────

const docFact = (over: Partial<Fact> = {}): Fact => ({
  fact_id: "f1", section: "labs", key: "lab:calcium", label: "SERUM CALCIUM", display: "8.9 mg/dL", value: {}, provenance: "document_unchecked", trust: "unchecked",
  sources: [{ type: "ocr", document_id: "d1", field_id: "r1", page_index: 0 }], flags: ["ocr_engines_agree"], ...over,
});

test("machine agreement is a separate label and only applies to unchecked document values", () => {
  assert.equal(ocrAgreement(docFact()), "engines_agree");
  assert.equal(machineAgreed(docFact()), true);
  assert.equal(machineAgreed(docFact({ trust: "checked", provenance: "document_confirmed" })), false); // a checked value is not "machine-agreed"
  assert.equal(ocrAgreement(docFact({ flags: ["disputed_reading", "ocr_engines_disagree"] })), "engines_disagree");
  assert.equal(ocrAgreement(docFact({ flags: [], sources: [{ type: "triage_form" }] })), null); // not a document value
  assert.equal(AGREEMENT_WORDS.engines_agree, "Machine-agreed — not yet checked");
});

test("the OCR agreement flags are not repeated as generic flag text", () => {
  for (const f of ["ocr_engines_agree", "ocr_engines_disagree", "ocr_single_reading"]) assert.equal(flagWords(f), null);
});

test("verification links open the existing documents workflow for that row or document", () => {
  assert.equal(verifyLink("c1", docFact()), "/intake/documents?case=c1&field=r1");
  assert.equal(verifyLink("c1", docFact({ sources: [{ type: "triage_form" }] })), null);
  assert.equal(documentLink("c1", "d9"), "/intake/documents?case=c1&doc=d9");
});

test("withheld reasons use the application's own codes; an unknown code still shows as a validation failure", () => {
  assert.equal(withheldReasonWords("source_not_verified"), "Source not verified by a person");
  assert.equal(withheldReasonWords("document_not_matched"), "Document not matched to this patient");
  assert.equal(withheldReasonWords("evidence_conflict"), "Evidence conflict — sources disagree");
  assert.match(withheldReasonWords("negation_or_time_uncertain"), /present, denied or in the past/);
  assert.match(withheldReasonWords("polarity_mismatch"), /^Validation failed/);
  assert.match(withheldReasonWords("diagnostic_language"), /^Validation failed/);
  assert.match(withheldReasonWords("something_new"), /^Validation failed \(something new\)/);
});

test("readiness never claims clinical completeness", () => {
  assert.match(READINESS_WORDS.ready_for_clinician_review, /not an approval/i);
  assert.equal(readinessReasonWords(["no_triage_run", "document_matching_pending"]).length, 2);
  assert.match(readinessReasonWords(["x_y"])[0], /x y/);
});

test("each reviewing role gets its own verification page; patients and supervisors get none", () => {
  assert.equal(verificationBase("anm"), "/intake/documents");
  assert.equal(verificationBase("medical_officer"), "/dashboard/verify");
  assert.equal(verificationBase("supervisor"), null);
  assert.equal(verificationBase("patient"), null);
  assert.equal(verificationBase(null), null);
  assert.equal(canOpenVerification("medical_officer"), true);
  assert.equal(verifyLink("c1", docFact(), "/dashboard/verify"), "/dashboard/verify?case=c1&field=r1");
  assert.equal(documentLink("c1", "d9", "/dashboard/verify"), "/dashboard/verify?case=c1&doc=d9");
});

test("withheld values are grouped by reason and section; sentences and documents stay separate; nothing is dropped", () => {
  const items = [
    { kind: "fact" as const, section: "labs", reason: "source_not_verified", fact_ids: ["a"] },
    { kind: "sentence" as const, section: "labs", reason: "diagnostic_language", fact_ids: ["a"] },
    { kind: "fact" as const, section: "labs", reason: "source_not_verified", fact_ids: ["b"] },
    { kind: "fact" as const, section: "labs", reason: "evidence_conflict", fact_ids: ["c", "d"] },
    { kind: "document" as const, section: "labs", reason: "document_not_matched", fact_ids: [], document_id: "d1" },
  ];
  const g = groupWithheld(items);
  assert.deepEqual(g.map((x) => [x.reason, x.fact_ids]), [["source_not_verified", ["a", "b"]], ["diagnostic_language", ["a"]], ["evidence_conflict", ["c", "d"]], ["document_not_matched", []]]);
  assert.deepEqual(items[0].fact_ids, ["a"]); // input not changed
});

test("route rule: only the medical officer opens /dashboard/verify; the intake documents step stays ANM/patient", async () => {
  const { allowedRoles } = await import("./auth.ts");
  assert.deepEqual(allowedRoles("/dashboard/verify"), ["medical_officer"]);
  assert.ok(!allowedRoles("/intake/documents")?.includes("medical_officer"));
  assert.deepEqual(allowedRoles("/dashboard"), ["medical_officer", "supervisor"]); // unchanged
});

// ── Schema .4: images, interview answers, the sectioned note (docs/plans/summary-note-all-sources.md) ──────────────

const imageFact = (over: Record<string, unknown> = {}): Fact => ({
  fact_id: "I1", section: "imaging", key: "image:d1", label: "Chest X-ray", display: "chest X-ray, AI visual description", provenance: "image_described_unacknowledged",
  trust: "unchecked", flags: [], sources: [{ type: "medical_image", document_id: "d1", model: "stub-model-1" }],
  value: { document_id: "d1", image_type: "chest_xray", status: "described", description: "text", structured_fields: { lung_fields: "clear", cardiac_silhouette: "normal size" },
           text_withheld: false, flags: [], confidence_band: "moderate", requires_acknowledgement: false, acknowledged: false, source_image_url: "/api/backend/x", note: null,
           disclaimer: "not a diagnosis", ...over },
});

test("an image's described fields are rows with plain labels, and only an unreviewed flag blocks", () => {
  const v = imageValue(imageFact())!;
  assert.deepEqual(imageFieldRows(v), [{ label: "Lung fields", text: "clear" }, { label: "Cardiac silhouette", text: "normal size" }]);
  assert.equal(imageNeedsReview(v), false);
  assert.equal(imageNeedsReview(imageValue(imageFact({ requires_acknowledgement: true }))!), true);
  assert.equal(imageNeedsReview(imageValue(imageFact({ requires_acknowledgement: true, acknowledged: true }))!), false);
  assert.deepEqual(imageFieldRows(imageValue(imageFact({ structured_fields: {} }))!), []);
  assert.equal(imageValue({ ...imageFact(), section: "labs" }), null);
  assert.equal(imageDescribedBy(v), null); // an older snapshot records no source: nothing is invented
  const demo = imageDescribedBy(imageValue(imageFact({ described_by: { provider: "fake", model: null, mode: "fake", synthetic: true } }))!)!;
  assert.equal(demo.demo, true);
  assert.match(demo.text, /^DEMO TEXT/);
  const real = imageDescribedBy(imageValue(imageFact({ described_by: { provider: "local", model: "medgemma-1.5", mode: "local", synthetic: false, validated_medical_device: false } }))!)!;
  assert.equal(real.text, "Described by medgemma-1.5 (local model; not a validated medical device).");
});

test("a flagged image that nobody reviewed is a danger item, ahead of the routine ones", () => {
  const items = ["pending_review", "image_flag_unacknowledged", "no_triage_run"].map((kind) => ({ kind, message: "", action: "", refs: [] }));
  assert.deepEqual(sortedAttention(items).map((a) => a.kind), ["image_flag_unacknowledged", "no_triage_run", "pending_review"]);
  assert.equal(attentionTone("image_flag_unacknowledged"), "danger");
});

test("image and interview sources are described in plain words, without inventing a model or a speaker", () => {
  assert.equal(sourceWords({ type: "medical_image", model: "stub-model-1", acknowledged_by_role: "medical_officer" }),
    "Uploaded image, described by stub-model-1 · marked as reviewed by medical officer");
  assert.equal(sourceWords({ type: "medical_image", model: null }), "Uploaded image, no AI description");
  assert.equal(sourceWords({ type: "interview_answer", speaker: "caregiver", answer_source: "typed", recorded_by_role: "anm" }),
    "Interview answer from a caregiver (typed by the health worker) · confirmed by health worker");
  assert.match(sourceWords({ type: "interview_answer", speaker: "patient", answer_source: "transcript", transcript_chars: [3, 20] }), /transcript characters 3–20/);
  assert.match(flagWords("unknown_answer")!, /still missing/);
  assert.equal(flagWords("image_flag"), null);
});

test("the note is shown only when the snapshot has one, and is built automatically once for a reviewer only", () => {
  assert.deepEqual(noteSections(summary()), []);
  const sections = [{ key: "urgency", title: "Urgency", lines: [{ kind: "status" as const, text: "x", fact_ids: [] }] }, { key: "empty", title: "Empty", lines: [] }];
  assert.deepEqual(noteSections(summary({ note_sections: sections })).map((x) => x.key), ["urgency"]);
  assert.equal(shouldAutoBuild({ case_id: "c", summary: null }, true, false), true);
  assert.equal(shouldAutoBuild({ case_id: "c", summary: null }, false, false), false); // a reader never triggers a build
  assert.equal(shouldAutoBuild({ case_id: "c", summary: null }, true, true), false); // only once
  assert.equal(shouldAutoBuild({ case_id: "c", summary: summary() }, true, false), false);
  assert.equal(shouldAutoBuild(null, true, false), false);
});


// ── the note on the health worker's final step (docs/plans/clinical-note-v2-plan.md step 2) ───────────────────────

test("open items are counted, danger signs first, and an empty list never claims the record is complete", () => {
  const pending = { kind: "ai_field", ref: "r", label: "l", state: "not_yet_checked", action: "a" };
  const miss = (key: string, danger: boolean) => ({ key, label: key, why_it_matters: "", is_danger_sign: danger, source: "intake_checklist" });
  const s = summary({
    pending_review: [pending, { ...pending, ref: "r2" }], conflicts: [{ key: "bp", label: "BP", fact_ids: [], values: [], action: "" }],
    missing_information: [miss("red_flag_screen", true), miss("duration", false)],
    attention: [{ kind: "image_flag_unacknowledged", message: "", action: "", refs: [] }],
  });
  const o = openItems(s);
  assert.deepEqual(o, { waiting: 2, conflicts: 1, missing: 2, dangerMissing: 1, flaggedImages: 1, total: 6 });
  const words = openItemsWords(o);
  assert.match(words[0], /danger-sign item is not recorded/); // danger signs lead
  assert.equal(words.length, 5);
  assert.deepEqual(openItemsWords(openItems(summary())), []);
  assert.ok(!openItemsWords(o).join(" ").match(/complete|normal/i));
});
