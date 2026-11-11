// Building a reviewer's correction of an AI-extracted value (POST /cases/{id}/ai/fields/{field_id}/review with
// outcome "corrected"; backend/app/ai/review.py CorrectedValue + _check). Pure: no React, no DOM.
// The backend stays authoritative (it re-validates and rejects identifiers); this only shapes and pre-checks input.
// Numbers are never rounded: rounding can move a value across a rule boundary.

export type CorrectionKind = "measurement" | "text" | "medication" | "symptom" | "red_flag";
export type DurationUnit = "hour" | "day" | "week" | "month" | "year";
export const DURATION_UNITS: readonly DurationUnit[] = ["hour", "day", "week", "month", "year"];
export type CorrectionForm = { value: string; value2: string; unit: string; negated: "" | "present" | "denied"; durationAmount?: string; durationUnit?: "" | DurationUnit };  // duration fields: symptoms only
export type CorrectionField = keyof CorrectionForm;
export type CorrectedValue = { value?: number | string; value2?: number; unit?: string; negated?: boolean; duration_amount?: number; duration_unit?: DurationUnit };
export type CorrectionResult = { ok: true; corrected: CorrectedValue } | { ok: false; errors: Partial<Record<CorrectionField, string>> };

const KINDS: readonly CorrectionKind[] = ["measurement", "text", "medication", "symptom", "red_flag"];

/** The correction form for this field kind, or null when the kind cannot be corrected here. */
export function correctionKindOf(kind: string): CorrectionKind | null {
  return (KINDS as readonly string[]).includes(kind) ? (kind as CorrectionKind) : null;
}

/** Start from the extracted value, so the reviewer edits rather than retypes. */
export function initialCorrection(kind: CorrectionKind, value: unknown): CorrectionForm {
  const f: CorrectionForm = { value: "", value2: "", unit: "", negated: "" };
  if (kind === "symptom") Object.assign(f, { durationAmount: "", durationUnit: "" });
  const o = value && typeof value === "object" ? (value as Record<string, unknown>) : null;
  if (kind === "text") f.value = typeof value === "string" ? value : "";
  if (kind === "measurement" && o) {
    f.value = o.value == null ? "" : String(o.value);
    f.value2 = o.value2 == null ? "" : String(o.value2);
    f.unit = typeof o.unit === "string" ? o.unit : "";
  }
  if (kind === "medication" && o) f.value = typeof o.name === "string" ? o.name : "";
  if ((kind === "symptom" || kind === "red_flag") && o && typeof o.negated === "boolean") f.negated = o.negated ? "denied" : "present";
  const d = kind === "symptom" && o && o.duration && typeof o.duration === "object" ? (o.duration as Record<string, unknown>) : null;
  if (d && typeof d.amount === "number" && (DURATION_UNITS as readonly unknown[]).includes(d.unit)) {
    f.durationAmount = String(d.amount);
    f.durationUnit = d.unit as DurationUnit;
  }
  return f;
}

const NUMBER = /^-?\d+(\.\d+)?$/;

export function buildCorrection(kind: CorrectionKind, form: CorrectionForm): CorrectionResult {
  const errors: Partial<Record<CorrectionField, string>> = {};
  if (kind === "measurement") {
    const v = form.value.trim();
    const v2 = form.value2.trim();
    const unit = form.unit.trim();
    if (!NUMBER.test(v)) errors.value = "Enter the number the patient confirms, for example 101.4";
    if (v2 && !NUMBER.test(v2)) errors.value2 = "Second value must be a number (for blood pressure, the lower number)";
    if (unit.length > 20) errors.unit = "Unit must be 20 characters or fewer";
    if (Object.keys(errors).length) return { ok: false, errors };
    const corrected: CorrectedValue = { value: Number(v) };
    if (v2) corrected.value2 = Number(v2);
    if (unit) corrected.unit = unit;
    return { ok: true, corrected };
  }
  if (kind === "text" || kind === "medication") {
    const v = form.value.trim();
    if (!v) errors.value = kind === "medication" ? "Enter the medicine name as the patient confirms it" : "Enter the corrected words";
    else if (v.length > 200) errors.value = "Keep the correction to 200 characters or fewer";
    return Object.keys(errors).length ? { ok: false, errors } : { ok: true, corrected: { value: v } };
  }
  if (!form.negated) return { ok: false, errors: { negated: "Choose whether the patient has it or denies it" } };
  const corrected: CorrectedValue = { negated: form.negated === "denied" };
  // A symptom's own duration: both the number and the unit, or neither. Never for a denied symptom.
  const amount = (form.durationAmount ?? "").trim();
  if (kind === "symptom" && form.negated === "present" && (amount || form.durationUnit)) {
    if (!NUMBER.test(amount) || Number(amount) <= 0 || Number(amount) > 1000) errors.durationAmount = "Enter how long as a number above 0, for example 2";
    if (!form.durationUnit) errors.durationUnit = "Choose hours, days, weeks, months or years";
    if (Object.keys(errors).length) return { ok: false, errors };
    corrected.duration_amount = Number(amount);
    corrected.duration_unit = form.durationUnit as DurationUnit;
  }
  return { ok: true, corrected };
}

/** One symptom as shown to the reviewer: the term when there is one, the words said, and its own duration. */
export function symptomWords(v: { name?: unknown; term?: unknown; negated?: unknown; duration?: unknown }): string {
  const name = typeof v.name === "string" ? v.name : "";
  const term = typeof v.term === "string" && v.term !== name.toLowerCase() ? v.term : null;
  const d = v.duration && typeof v.duration === "object" ? (v.duration as { text?: unknown; amount?: unknown; unit?: unknown }) : null;
  const how = !d || v.negated ? ""
    : typeof d.amount === "number" && typeof d.unit === "string" ? `, ${d.amount} ${d.unit}${d.amount === 1 ? "" : "s"}`
    : typeof d.text === "string" ? `, ${d.text} (as said)` : "";
  return `${v.negated ? "denies " : ""}${term ?? name}${term ? ` (said: ${name})` : ""}${how}`;
}
