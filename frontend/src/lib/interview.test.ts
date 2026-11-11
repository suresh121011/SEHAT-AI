// Run: node --test src/lib/interview.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  answerText, buildAnswer, emptyDraft, firstUnanswered, interviewErrorWords, isKnown, patientQuestions, progress, workerTasks,
  type InterviewAnswer, type InterviewQuestion, type InterviewView,
} from "./interview.ts";

const ans = (over: Partial<InterviewAnswer> = {}): InterviewAnswer => ({
  choice: null, text: "cough", source: "typed", transcription_id: null, char_start: null, char_end: null, speaker: "patient", is_unknown: false,
  recorded_by_role: "anm", recorded_at: "t", status: "checked_by_health_worker", ...over,
});
const q = (key: string, over: Partial<InterviewQuestion> = {}): InterviewQuestion => ({
  key, label: key, question_text: `${key}?`, kind: "text", options: [], audience: "patient", answerable: true, is_danger_sign: false, answered: false, answer: null, ...over,
});
const view = (questions: InterviewQuestion[]): InterviewView => ({
  case_id: "c", script_version: "v", language: "en", notice: "", answers_hidden: false, questions, still_missing: [],
});
const screen = q("red_flag_screen", { audience: "worker", answerable: false, is_danger_sign: true, kind: "choice", options: ["none present"] });
const adherence = q("adherence", { kind: "choice", options: ["no doses missed", "some missed", "not sure"] });

test("measurements and danger-sign screens are tasks, never interview questions", () => {
  const v = view([screen, q("chief_complaint"), q("spo2", { audience: "worker", answerable: false })]);
  assert.deepEqual(patientQuestions(v).map((x) => x.key), ["chief_complaint"]);
  assert.deepEqual(workerTasks(v).map((x) => x.key), ["red_flag_screen", "spo2"]);
});

test("a not-sure answer is recorded but never counts as known, and the interview is done only when every question has an answer", () => {
  const unsure = { ...adherence, answered: true, answer: ans({ choice: "not sure", text: null, is_unknown: true }) };
  const known = q("chief_complaint", { answered: true, answer: ans() });
  assert.equal(isKnown(unsure), false);
  assert.equal(isKnown(known), true);
  assert.deepEqual(progress(view([known, unsure, q("duration")])), { total: 3, known: 1, unknown: 1, unanswered: 1, done: false });
  assert.deepEqual(progress(view([known, unsure])), { total: 2, known: 1, unknown: 1, unanswered: 0, done: true });
  assert.equal(progress(view([screen])).done, false); // nothing to ask is not "interview complete"
  assert.equal(firstUnanswered(view([known, q("duration")])), 1);
  assert.equal(firstUnanswered(view([known])), -1);
});

test("an answer is never sent without a valid value and the confirmation tick", () => {
  const text = q("chief_complaint");
  assert.deepEqual(buildAnswer(text, { ...emptyDraft(text), text: "  " }), { ok: false, error: "Enter the answer as it was said." });
  assert.equal(buildAnswer(text, { ...emptyDraft(text), text: "x".repeat(501), confirmed: true }).ok, false);
  assert.match((buildAnswer(text, { ...emptyDraft(text), text: "cough" }) as { error: string }).error, /Tick the box/);
  assert.deepEqual(buildAnswer(text, { ...emptyDraft(text), text: " cough ", confirmed: true }),
    { ok: true, body: { question_key: "chief_complaint", speaker: "patient", confirm: true, text: "cough" } });
  assert.equal(buildAnswer(adherence, { ...emptyDraft(adherence), choice: "made up", confirmed: true }).ok, false); // only a listed option
  assert.deepEqual(buildAnswer(adherence, { ...emptyDraft(adherence), choice: "some missed", speaker: "caregiver", confirmed: true }),
    { ok: true, body: { question_key: "adherence", speaker: "caregiver", confirm: true, choice: "some missed" } });
});

test("an answer taken from a voice recording cites that transcript; a choice never does", () => {
  const text = q("chief_complaint");
  const transcript = { transcription_id: "t1", text: "I have a cough" };
  const r = buildAnswer(text, { ...emptyDraft(text), text: "I have a cough", confirmed: true, transcript });
  assert.deepEqual(r.ok && r.body.source, { transcription_id: "t1", char_start: 0, char_end: 14 });
  const c = buildAnswer(adherence, { ...emptyDraft(adherence), choice: "some missed", confirmed: true, transcript });
  assert.equal(c.ok && c.body.source, undefined);
});

test("editing starts from the recorded answer but always needs a fresh confirmation", () => {
  const d = emptyDraft(q("chief_complaint", { answered: true, answer: ans({ speaker: "caregiver" }) }));
  assert.deepEqual(d, { choice: null, text: "cough", speaker: "caregiver", confirmed: false, transcript: null });
  assert.equal(answerText(null), "Not answered");
  assert.equal(answerText(ans({ choice: "some missed", text: null })), "some missed");
});

test("every refusal says that nothing was saved", () => {
  for (const code of ["PII_DETECTED", "NOT_IN_SCRIPT", "RECORD_ON_TRIAGE_FORM", "ANSWER_INVALID", "CONSENT_NOT_EFFECTIVE", "RATE_LIMITED", "SOMETHING_NEW"]) {
    assert.match(interviewErrorWords(code, 400, "Could not save."), /Nothing was saved\.$/);
  }
  assert.match(interviewErrorWords(undefined, 403, "x"), /cannot record/);
});
