// Run: node --test src/lib/review.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  barShare,
  clockOffsetMs,
  describeChange,
  escalationView,
  filterQueue,
  formatClock,
  formatRate,
  formatWaiting,
  isStale,
  openEscalations,
  remainingMs,
  serverOrderLooksValid,
  type Escalation,
  type QueueItem,
} from "./review.ts";
import {
  correctionFieldControl,
  correctionLine,
  correctionSuccess,
  formatValue,
  missingLabel,
  overrideFieldControl,
  priorityExplanation,
  provenanceNote,
  type Correction,
  type FieldProvenance,
} from "./review.ts";

function item(over: Partial<QueueItem>): QueueItem {
  return {
    case_id: "c",
    patient_token: "PT-X",
    facility_code: "PHC",
    scenario: "opd",
    triage_run_id: "r",
    triaged_at: "2026-10-04T10:00:00.000000+00:00",
    waiting_seconds: 0,
    determination: "complete",
    needs_human_review: true,
    missing_fields: [],
    triggered_rule_ids: [],
    consent_triage: "granted",
    rules_urgency: "GREEN",
    effective_urgency: "GREEN",
    effective_urgency_source: "rules_engine",
    priority_urgency: "GREEN",
    priority_source: "rules_engine",
    override_count: 0,
    review_status: "awaiting_review",
    signed_off_at: null,
    escalation: null,
    ...over,
  };
}

function esc(state: Escalation["state"], deadline: string): Escalation {
  return { state, anchor: "", anchor_source: "triage_run", deadline, window_seconds: 180, acknowledged_at: null, acknowledged_by_role: null, acknowledged_via: null, acknowledged_late: null, notification_sent: false };
}

test("countdown uses the server clock offset, not the raw client clock", () => {
  // Client clock is 60 s behind the server.
  const received = Date.parse("2026-10-04T10:00:00Z");
  const offset = clockOffsetMs("2026-10-04T10:01:00Z", received);
  assert.equal(offset, 60_000);
  // Deadline at 10:03 server time; client reads 10:00:30 → server 10:01:30 → 90 s left (not 150 s).
  assert.equal(remainingMs("2026-10-04T10:03:00Z", Date.parse("2026-10-04T10:00:30Z"), offset), 90_000);
});

test("a page reload does not reset the countdown: it is derived from the server deadline", () => {
  const deadline = "2026-10-04T10:03:00Z";
  const before = remainingMs(deadline, Date.parse("2026-10-04T10:02:00Z"), 0);
  const afterReload = remainingMs(deadline, Date.parse("2026-10-04T10:02:00Z"), clockOffsetMs("2026-10-04T10:02:00Z", Date.parse("2026-10-04T10:02:00Z")));
  assert.equal(before, afterReload);
  assert.equal(before, 60_000);
});

test("escalation view: server state wins; a passed deadline before server confirmation is flagged, not claimed", () => {
  const deadline = "2026-10-04T10:03:00Z";
  const now = Date.parse("2026-10-04T10:04:00Z");
  assert.deepEqual(escalationView(esc("pending", deadline), now, 0), { display: "past_target", remainingMs: -60_000, serverConfirmed: false });
  assert.equal(escalationView(esc("overdue", deadline), now, 0).display, "overdue");
  assert.equal(escalationView(esc("acknowledged", deadline), now, 0).display, "acknowledged");
  assert.equal(escalationView(esc("pending", deadline), Date.parse("2026-10-04T10:01:00Z"), 0).display, "pending");
});

test("clock formatting", () => {
  assert.equal(formatClock(125_000), "2:05");
  assert.equal(formatClock(-30_400), "0:30");
  assert.equal(formatClock(0), "0:00");
  assert.equal(formatClock(3_725_000), "1:02:05");
  assert.equal(formatWaiting(30), "under 1 min");
  assert.equal(formatWaiting(4 * 60 + 10), "4 min");
  assert.equal(formatWaiting(125 * 60), "2 h 5 min");
  assert.equal(formatWaiting(-1), "unknown");
});

test("stale data is detected on the server clock", () => {
  const gen = "2026-10-04T10:00:00Z";
  assert.equal(isStale(gen, Date.parse("2026-10-04T10:00:30Z"), 0), false);
  assert.equal(isStale(gen, Date.parse("2026-10-04T10:01:00Z"), 0), true);
  assert.equal(isStale("not a date", Date.now(), 0), true);
});

test("filtering keeps the server order and never re-sorts", () => {
  const items = [
    item({ case_id: "a", priority_urgency: "RED", triaged_at: "2026-10-04T10:05:00Z" }),
    item({ case_id: "b", priority_urgency: "YELLOW", triaged_at: "2026-10-04T09:00:00Z" }),
    item({ case_id: "c", priority_urgency: "YELLOW", triaged_at: "2026-10-04T09:30:00Z" }),
    item({ case_id: "d", priority_urgency: "GREEN", triaged_at: "2026-10-04T08:00:00Z" }),
  ];
  assert.deepEqual(filterQueue(items, "all").map((i) => i.case_id), ["a", "b", "c", "d"]);
  assert.deepEqual(filterQueue(items, "YELLOW").map((i) => i.case_id), ["b", "c"]);
  assert.equal(serverOrderLooksValid(items), true);
  assert.equal(serverOrderLooksValid([items[1], items[0]]), false);
  assert.equal(serverOrderLooksValid([items[2], items[1]]), false);
});

test("an AI failure or low AI confidence has no field that could reorder a case", () => {
  // The queue item carries no AI score or confidence; order depends only on server priority + time.
  const keys = Object.keys(item({}));
  for (const k of keys) assert.ok(!/confidence|ai_|score|llm/i.test(k), `unexpected AI-derived key ${k}`);
});

test("filtering by urgency uses the priority (never-lowered) urgency, not the reviewer-recorded one", () => {
  const lowered = item({ case_id: "x", rules_urgency: "RED", effective_urgency: "YELLOW", priority_urgency: "RED", effective_urgency_source: "reviewer_override" });
  assert.deepEqual(filterQueue([lowered], "RED").map((i) => i.case_id), ["x"]);
  assert.deepEqual(filterQueue([lowered], "YELLOW"), []);
});

test("open escalations: every unacknowledged RED, whatever the tab", () => {
  const items = [
    item({ case_id: "p", priority_urgency: "RED", escalation: esc("pending", "2026-10-04T10:03:00Z") }),
    item({ case_id: "o", priority_urgency: "RED", escalation: esc("overdue", "2026-10-04T10:03:00Z") }),
    item({ case_id: "k", priority_urgency: "RED", escalation: esc("acknowledged", "2026-10-04T10:03:00Z") }),
    item({ case_id: "g" }),
  ];
  assert.deepEqual(openEscalations(items).map((i) => i.case_id), ["p", "o"]);
  assert.deepEqual(filterQueue(items, "escalations").map((i) => i.case_id), ["p", "o"]);
});

test("governance formatting: missing data is not zero; zero is shown with its denominator", () => {
  assert.equal(formatRate(null, 0, 0), "No data (0 cases in period)");
  assert.equal(formatRate(0, 0, 5), "0.0% (0 of 5)");
  assert.equal(formatRate(0.25, 1, 4), "25.0% (1 of 4)");
  assert.equal(barShare(0, [0, 0, 0]), 0);
  assert.equal(barShare(2, [1, 2, 4]), 0.5);
});

test("counterfactual wording restates the server's change without computing anything", () => {
  assert.equal(describeChange({ field: "vitals.spo2", from: 88, to: 92 }), "If oxygen saturation (SpO2) were 92 instead of 88");
  assert.equal(describeChange({ field: "red_flags_present", remove: "severe_pain" }), "If the red flag “severe pain” had not been recorded");
  assert.equal(describeChange({ field: "red_flag_screen_completed", to: false }), "If the red-flag screen had not been completed");
});

import { applyVitalEdits } from "./review.ts";

test("correcting vitals preserves every other recorded field and never rounds", () => {
  const stored = {
    scenario: "maternal", pregnant: true, age_years: 24, red_flag_screen_completed: true, red_flags_present: ["severe_pain"],
    vitals: { spo2: 91, pulse: 110, temp_c: 39.04, spo2_scale: 1 }, maternal: { danger_sign_screen_completed: true, danger_signs: ["high_fever"], hb_g_dl: 7.1 },
  };
  const r = applyVitalEdits(stored, { spo2: "95", temp_c: "39.04", pulse: "" });
  assert.ok(r.ok);
  if (!r.ok) return;
  assert.deepEqual(r.changed, ["spo2", "pulse"]);
  assert.deepEqual(r.input.maternal, stored.maternal);
  assert.deepEqual(r.input.red_flags_present, ["severe_pain"]);
  assert.deepEqual(r.input.vitals, { spo2: 95, temp_c: 39.04, spo2_scale: 1 });
  assert.equal(stored.vitals.spo2, 91, "the stored input object is not mutated");
});

test("vital edits are range-checked like the backend", () => {
  const r = applyVitalEdits({ vitals: { sbp: 120 } }, { spo2: "101", dbp: "130", temp_c: "39.123" });
  assert.equal(r.ok, false);
  if (r.ok) return;
  assert.ok(r.errors.spo2 && r.errors.temp_c);
  const bp = applyVitalEdits({ vitals: { sbp: 120 } }, { dbp: "130" });
  assert.equal(bp.ok, false);
});

// ── Phase 8 hardening: queue wording, corrections, provenance ──

test("missing pill never says 0 missing", () => {
  assert.equal(missingLabel(item({ determination: "insufficient_data", missing_fields: ["vitals.spo2", "vitals.pulse"] })), "2 missing");
  assert.equal(missingLabel(item({ determination: "insufficient_data", missing_fields: [], consent_triage: "withdrawn" })), "Needs information (details hidden)");
  assert.equal(missingLabel(item({ determination: "insufficient_data", missing_fields: [] })), "Needs information");
  assert.equal(missingLabel(item({ determination: "complete" })), null);
});

test("priority explanation names an open earlier RED before override wording", () => {
  assert.equal(
    priorityExplanation(item({ priority_source: "open_red_escalation", priority_urgency: "RED", rules_urgency: "GREEN", effective_urgency: "GREEN" })),
    "Stays RED in the queue until a medical officer signs it off, because an earlier RED was raised (latest rules result: GREEN). Acknowledging alone does not lower it.",
  );
  assert.match(priorityExplanation(item({ priority_source: "reviewer_override_raise", priority_urgency: "RED", rules_urgency: "YELLOW", effective_urgency: "RED" })) ?? "", /Raised by reviewer to RED/);
  assert.match(priorityExplanation(item({ priority_urgency: "RED", rules_urgency: "RED", effective_urgency: "YELLOW" })) ?? "", /lowering never moves a case down/);
  assert.equal(priorityExplanation(item({})), null);
});

test("formatValue says not recorded for missing values, never normal", () => {
  assert.equal(formatValue(null), "not recorded");
  assert.equal(formatValue(undefined), "not recorded");
  assert.equal(formatValue(92), "92");
  assert.equal(formatValue(true), "yes");
  assert.equal(formatValue([]), "none");
  assert.equal(formatValue(["chest_pain"]), "chest pain");
});

test("provenance: corrected field, carried-over field, and nothing stored", () => {
  const fp: FieldProvenance = {
    "vitals.spo2": { source: "reviewer_correction", previous_value: 95, from_run_id: "aaaaaaaa1111", created_at: "2026-10-04T10:05:00Z", actor_role: "medical_officer", is_current_user: true, reason_code: "remeasured", reason_label: "Re-measured" },
    "*": { source: "unchanged_from_previous_run", from_run_id: "aaaaaaaa1111" },
  };
  const c = provenanceNote("vitals.spo2", fp);
  assert.equal(c?.kind, "corrected");
  assert.match(c?.text ?? "", /^Changed by Medical officer \(you\) at \d\d:\d\d — Re-measured; was 95$/);
  assert.deepEqual(provenanceNote("vitals.pulse", fp), { kind: "unchanged", text: "Unchanged since the earlier check" });
  assert.equal(provenanceNote("vitals.pulse", {}), null);
  assert.equal(provenanceNote("vitals.pulse", null), null);
  assert.equal(provenanceNote("vitals.pulse", undefined), null);
  const prevMissing: FieldProvenance = { "vitals.sbp": { source: "reviewer_correction", previous_value: null, from_run_id: "r", created_at: "2026-10-04T10:05:00Z", actor_role: "medical_officer", is_current_user: false, reason_code: "other", reason_label: null } };
  assert.match(provenanceNote("vitals.sbp", prevMissing)?.text ?? "", /Medical officer at .* — other; was not recorded$/);
  assert.equal(provenanceNote("vitals.pulse", prevMissing), null); // no "*" → no claim about other fields
});

function corr(over: Partial<Correction>): Correction {
  return {
    triage_run_id: "bbbbbbbb2222",
    corrects_run_id: "aaaaaaaa1111",
    urgency_before: "YELLOW",
    urgency_after: "RED",
    changed_fields: ["vitals.spo2"],
    changes: [{ field: "vitals.spo2", old: 95, new: 88 }],
    reason_code: "remeasured",
    reason_label: "Re-measured",
    reason_text: null,
    actor_role: "medical_officer",
    is_current_user: true,
    created_at: "2026-10-04T10:05:00Z",
    ...over,
  };
}

test("correction history line shows values only when the server sent them", () => {
  assert.equal(correctionLine(corr({})), "Correction: run aaaaaaaa → run bbbbbbbb, Re-measured; changed: oxygen saturation (SpO2) (95 → 88)");
  assert.equal(correctionLine(corr({ changes: null })), "Correction: run aaaaaaaa → run bbbbbbbb, Re-measured; changed: oxygen saturation (SpO2)");
  assert.match(correctionLine(corr({ changes: [{ field: "vitals.temp_c", old: 38.5, new: null }] })), /temperature \(38\.5 → not recorded\)/);
});

test("correction success text comes from the response and flags a lower result", () => {
  const base = { case_id: "c", corrects_run_id: "aaaaaaaa1111", triage_run_id: "bbbbbbbb2222", changed_fields: ["vitals.spo2"], reason_code: "entry_error", corrected_at: "2026-10-04T10:05:00Z", reviewer_role: "medical_officer" };
  const down = correctionSuccess({ ...base, urgency_before: "RED", urgency: "YELLOW" });
  assert.equal(down.lowered, true);
  assert.match(down.message, /RED → YELLOW/);
  assert.match(down.message, /The new rules result is lower; the case needs a fresh sign-off\.$/);
  const up = correctionSuccess({ ...base, urgency_before: "GREEN", urgency: "RED" });
  assert.equal(up.lowered, false);
  assert.doesNotMatch(up.message, /lower/);
});

test("server validation paths map to dialog controls; unknown paths go to the summary", () => {
  assert.equal(correctionFieldControl("reason_code"), "reason_code");
  assert.equal(correctionFieldControl("reason_text"), "reason_text");
  assert.equal(correctionFieldControl("input.vitals.spo2"), "spo2");
  assert.equal(correctionFieldControl("input.vitals.temp_c"), "temp_c");
  assert.equal(correctionFieldControl("input.vitals.consciousness"), null);
  assert.equal(correctionFieldControl("input.vitals"), null);
  assert.equal(correctionFieldControl(""), null);
  assert.equal(overrideFieldControl("new_urgency"), "new_urgency");
  assert.equal(overrideFieldControl("expected_urgency"), null);
});

test("voice field words reuse the shared vocabulary", async () => {
  const { voiceFieldWords, voiceSourceWords } = await import("./review.ts");
  assert.equal(voiceFieldWords("spo2"), "oxygen saturation (SpO2)");
  assert.equal(voiceFieldWords("bp"), "blood pressure");
  assert.equal(voiceFieldWords("temp"), "temperature");
  assert.equal(voiceFieldWords("resp_rate"), "breathing rate");
  assert.equal(voiceFieldWords("symptom_duration"), "symptom duration");
  assert.equal(voiceSourceWords("voice_manual_correction"), "Voice, corrected by a person");
  assert.equal(voiceSourceWords("voice_transcript"), "Voice-transcribed, confirmed by a person");
});
