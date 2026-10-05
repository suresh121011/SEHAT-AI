// Run: node --test src/lib/medicalImages.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  findingsSourceLabel,
  IMAGE_TYPES,
  aiAvailabilityNote,
  imageTypeAvailability,
  confidenceBand,
  hasRedFlag,
  hasYellowFlag,
  humanizeField,
  imageTypeLabel,
  needsAck,
  notedSignals,
  pendingAckCount,
  raisingSignals,
  safeImageUrl,
  statusCopy,
  visibleFields,
  withheldSummary,
  type UrgencySignal,
} from "./medicalImages.ts";

function sig(over: Partial<UrgencySignal>): UrgencySignal {
  return { signal: "ST_ELEVATION", action: "RED_FLAG", note: "", source: "MedGemma image analysis", rule_set: "ecg_strip", negated: false, ...over };
}

test("the five image types have labels and icons", () => {
  assert.deepEqual(
    IMAGE_TYPES.map((t) => t.key),
    ["chest_xray", "ecg_strip", "ct_report_image", "wound_photo", "skin_lesion"],
  );
  assert.equal(imageTypeLabel("ct_report_image"), "CT scan image");
  assert.equal(imageTypeLabel("something_else"), "something else");
  assert.equal(imageTypeLabel(null), "Unknown type");
});

test("confidence band falls back to thresholds and prefers the server's band", () => {
  assert.equal(confidenceBand(0.81), "high");
  assert.equal(confidenceBand(0.8), "moderate");
  assert.equal(confidenceBand(0.5), "moderate");
  assert.equal(confidenceBand(0.49), "low");
  assert.equal(confidenceBand(null), null);
  assert.equal(confidenceBand(Number.NaN), null);
  assert.equal(confidenceBand(0.95, "low"), "low");
});

test("only non-negated RED/YELLOW flags raise attention", () => {
  assert.equal(hasRedFlag([sig({})]), true);
  assert.equal(hasRedFlag([sig({ negated: true })]), false);
  assert.equal(hasRedFlag([sig({ action: "REVIEW_NOTE" })]), false);
  assert.equal(hasRedFlag([sig({ action: "YELLOW_FLAG" })]), false);
  assert.equal(hasRedFlag(null), false);
  assert.equal(hasYellowFlag([sig({ action: "YELLOW_FLAG" })]), true);
  assert.equal(hasYellowFlag([sig({ action: "YELLOW_FLAG", negated: true })]), false);
});

test("negated and REVIEW_NOTE signals are listed as noted, never as raising", () => {
  const all = [sig({}), sig({ signal: "PNEUMOTHORAX", negated: true }), sig({ signal: "X", action: "REVIEW_NOTE" })];
  assert.deepEqual(notedSignals(all).map((s) => s.signal), ["PNEUMOTHORAX", "X"]);
  assert.deepEqual(raisingSignals(all).map((s) => s.signal), ["ST_ELEVATION"]);
});

test("status copy explains each not-available reason in plain words", () => {
  assert.equal(statusCopy({ status: "described", not_available_reason: null, note: null }), null);
  assert.equal(
    statusCopy({ status: "not_available", not_available_reason: "disabled", note: null }),
    "Medical image AI is not enabled. Your image has been saved for the doctor to review directly.",
  );
  assert.match(statusCopy({ status: "not_available", not_available_reason: "consent_ai_assist_missing", note: null }) ?? "", /AI assistance was not agreed/);
  assert.match(statusCopy({ status: "not_available", not_available_reason: "synthetic_attestation_missing", note: null }) ?? "", /synthetic/);
  assert.equal(statusCopy({ status: "not_available", not_available_reason: null, note: "server note" }), "server note");
  assert.equal(statusCopy({ status: "failed", not_available_reason: null, note: null }), "The image could not be described; it has been saved for the reviewer.");
  assert.match(statusCopy({ status: "unsupported_type", not_available_reason: null, note: null }) ?? "", /saved for the reviewer/);
});

test("status copy never uses diagnostic wording", () => {
  for (const status of ["not_available", "failed", "unsupported_type"] as const) {
    for (const reason of ["disabled", "consent_ai_assist_missing", "synthetic_attestation_missing", null] as const) {
      const text = statusCopy({ status, not_available_reason: reason, note: null }) ?? "";
      assert.doesNotMatch(text, /diagnos/i);
    }
  }
});

test("pending acknowledgement counts only required and not yet acknowledged images", () => {
  const items = [
    { requires_acknowledgement: true, acknowledged: false },
    { requires_acknowledgement: true, acknowledged: true },
    { requires_acknowledgement: false, acknowledged: false },
  ];
  assert.equal(pendingAckCount(items), 1);
  assert.equal(needsAck(items), true);
  assert.equal(needsAck(items.slice(1)), false);
  assert.equal(needsAck(null), false);
});

test("field labels are humanised", () => {
  assert.equal(humanizeField("rate_bpm"), "Rate (bpm)");
  assert.equal(humanizeField("st_segment"), "ST segment");
  assert.equal(humanizeField("qrs_duration_ms"), "QRS duration (ms)");
  assert.equal(humanizeField("qtc_ms"), "QTc (ms)");
  assert.equal(humanizeField("lung_fields"), "Lung fields");
  assert.equal(humanizeField("bpm"), "Bpm");
});

test("withheld fields are never shown even if a value is present", () => {
  const rows = visibleFields({ structured_fields: { rate_bpm: "72", impression: "x", rhythm: " " }, withheld: { fields: ["impression"], description: false, reasons: [] } });
  assert.deepEqual(rows, [["rate_bpm", "72"]]);
});

test("withheld summary gives counts and reasons, never text", () => {
  assert.equal(withheldSummary({ fields: [], description: false, reasons: [] }), null);
  assert.equal(withheldSummary(null), null);
  assert.equal(
    withheldSummary({ fields: ["impression"], description: false, reasons: ["diagnostic_language"] }),
    "1 field withheld by the non-diagnostic filter (diagnostic language). Withheld text is never shown.",
  );
  assert.match(withheldSummary({ fields: ["a", "b"], description: true, reasons: [] }) ?? "", /^2 fields and the description withheld/);
  assert.match(withheldSummary({ fields: [], description: true, reasons: [] }) ?? "", /^The description withheld/);
});

test("only same-origin proxy image URLs are used", () => {
  assert.equal(safeImageUrl("/api/backend/cases/c1/medical-images/d1/image"), "/api/backend/cases/c1/medical-images/d1/image");
  assert.equal(safeImageUrl("https://evil.example/x.png"), null);
  assert.equal(safeImageUrl("/api/backend/../session"), null);
  assert.equal(safeImageUrl("//evil.example/api/backend/x"), null);
  assert.equal(safeImageUrl(undefined), null);
});

test("image types stay uploadable when supported even if the AI is off; older servers disable them with a reason", () => {
  const base = { ocr_enabled: true, document_types: { lab_report: true } };
  assert.deepEqual(imageTypeAvailability({ ...base, supported_image_types: ["ecg_strip"], medgemma_enabled: false }, "ecg_strip"), { ok: true, reason: null });
  assert.equal(imageTypeAvailability({ ...base, supported_image_types: ["ecg_strip"] }, "skin_lesion").ok, false);
  assert.deepEqual(imageTypeAvailability({ ...base, document_types: { chest_xray: true } }, "chest_xray"), { ok: true, reason: null });
  assert.equal(imageTypeAvailability(base, "chest_xray").reason, "this server does not accept medical images yet");
  assert.equal(imageTypeAvailability({ ...base, ocr_enabled: false, supported_image_types: ["chest_xray"] }, "chest_xray").ok, false);
  assert.equal(imageTypeAvailability(null, "chest_xray").ok, false);
});

test("AI availability note follows the capabilities", () => {
  const base = { ocr_enabled: true, document_types: {} };
  assert.match(aiAvailabilityNote({ ...base, medgemma_enabled: false }) ?? "", /not enabled/);
  assert.match(aiAvailabilityNote({ ...base, medgemma_enabled: true, medgemma_cloud: true, medgemma_backend: "google_ai" }) ?? "", /synthetic.*cloud AI service \(google_ai/);
  assert.match(aiAvailabilityNote({ ...base, medgemma_enabled: true, medgemma_backend: "fake" }) ?? "", /not a real model/);
  assert.equal(aiAvailabilityNote({ ...base, ocr_enabled: false }), null);
});

test("findingsSourceLabel never presents demo text as model output", () => {
  assert.match(findingsSourceLabel({ backend: "fake", provenance: { provider: "fake", model: "fake-canned-v1", mode: "fake", synthetic: true } }), /^DEMO TEXT/);
  assert.match(findingsSourceLabel({ backend: "fake" }), /^DEMO TEXT/); // older server without provenance
  assert.match(findingsSourceLabel({ backend: "google_ai", provenance: { provider: "google_ai", model: "m", mode: "cloud", synthetic: false } }), /^Cloud AI model/);
  assert.equal(findingsSourceLabel({ backend: null, provenance: { provider: null, model: null, mode: "none", synthetic: false } }), "No AI model was used");
});

test("findingsSourceLabel: unknown mode is not reported as 'no AI'", () => {
  assert.match(findingsSourceLabel({ backend: "something", provenance: { provider: "something", model: null, mode: "unknown", synthetic: false } }), /unknown/);
});
