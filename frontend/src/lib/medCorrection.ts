// Prescription-line display and correction (backend app/ocr/service.py: med_disputed_parts, Correction). Pure, so it is
// testable with node --test. A strength the two OCR engines read differently is never shown as the dose: Chandra read a
// handwritten "800mg" as "500mg" on a real prescription, and the screen used to show 500 mg.

export type MedLine = {
  drug_raw?: string | null;
  strength_raw?: string | null;
  dosage_pattern?: string | null;
  frequency_raw?: string | null;
  duration_raw?: string | null;
  line_raw?: string | null;
  strength_status?: "readings_differ" | "read" | "not_read";
  disputed_parts?: string[];
};

export const STRENGTH_UNCLEAR = "strength unclear — check the paper";

/** The machine reading of one line, with a disputed strength replaced by a warning. */
export function medLineText(f: MedLine): string {
  const strength = f.strength_status === "readings_differ" ? `⚠ ${STRENGTH_UNCLEAR}` : f.strength_raw;
  const parts = [strength, f.dosage_pattern, f.frequency_raw, f.duration_raw].filter(Boolean);
  return parts.length ? parts.join(" · ") : f.line_raw ?? "";
}

// Closed vocabularies, mirroring the backend Correction model (no free text).
export const FREQUENCIES = ["OD", "BD", "TDS", "QID", "5 times a day", "HS", "SOS", "STAT", "PRN"] as const;
export const DURATION_UNITS = ["days", "weeks", "months"] as const;
const STRENGTH = /^\d{1,5}(\.\d{1,3})?\s?(mg|mcg|g|ml|iu|%)$/i;
const PATTERN = /^[0-2½](-[0-2½]){2,3}$/;

export type MedInput = { strength: string; pattern: string; frequency: string; durationNumber: string; durationUnit: string };

/** Build the correction body, or explain what is wrong. A disputed strength must be typed. */
export function buildMedCorrection(input: MedInput, strengthRequired: boolean): { correction: Record<string, string> } | { error: string } {
  const c: Record<string, string> = {};
  const strength = input.strength.trim().replace(/\s+/g, " ");
  if (strength) {
    if (!STRENGTH.test(strength)) return { error: "Type the strength as written, e.g. 800 mg, 500 mcg or 10 ml." };
    c.strength_raw = strength.replace(/(mg|mcg|g|ml|iu)$/i, (u) => u.toLowerCase());
  } else if (strengthRequired) {
    return { error: "The two readings of the strength differ. Type the strength exactly as written on the paper." };
  }
  const pattern = input.pattern.trim();
  if (pattern) {
    if (!PATTERN.test(pattern)) return { error: "Dose pattern looks like 1-0-1 (morning-afternoon-night)." };
    c.dosage_pattern = pattern;
  }
  if (input.frequency) {
    if (!(FREQUENCIES as readonly string[]).includes(input.frequency)) return { error: "Choose the frequency from the list." };
    c.frequency_raw = input.frequency;
  }
  const n = input.durationNumber.trim();
  if (n) {
    if (!/^\d{1,3}$/.test(n) || Number(n) < 1) return { error: "Duration is a whole number, e.g. 7." };
    const unit = (DURATION_UNITS as readonly string[]).includes(input.durationUnit) ? input.durationUnit : "days";
    c.duration_raw = `${Number(n)} ${Number(n) === 1 ? unit.replace(/s$/, "") : unit}`;
  }
  if (!Object.keys(c).length) return { error: "Type at least one value from the paper." };
  return { correction: c };
}
