// Triage form state → strict backend TriageInput (backend/app/rules/models.py). Pure: no React, no DOM, so it is
// unit-tested with `node --test`. The backend stays authoritative: this only shapes and pre-checks input, using the
// backend's own ranges and cross-field rules; it never computes urgency.

export const SCENARIOS = ["opd", "maternal", "chronic_ncd", "health_camp", "campus_fever", "occupational", "referral"] as const;
export type Scenario = (typeof SCENARIOS)[number];

// AtpFlag values (backend/app/rules/models.py) with plain-language labels for the red-flag screen.
export const ATP_FLAGS = [
  ["stridor", "Noisy, high-pitched breathing (stridor)"],
  ["angioedema_face", "Swelling of face, lips or tongue"],
  ["active_seizure", "Fitting now (active seizure)"],
  ["incomplete_sentences", "Too breathless to finish a sentence"],
  ["audible_wheeze", "Wheeze you can hear"],
  ["active_bleeding", "Active bleeding"],
  ["chest_pain_acute_24h", "Chest pain that started in the last 24 hours"],
  ["limb_weakness_24h", "New weakness of an arm or leg (last 24 hours)"],
  ["stroke_suspected_24h", "Signs of stroke (last 24 hours)"],
  ["dangerous_mechanism_trauma", "Injury from a dangerous accident"],
  ["sob_acute_12h", "Breathlessness that started in the last 12 hours"],
  ["limb_ischaemia_48h", "Cold, pale or painful limb (last 48 hours)"],
  ["allergic_reaction", "Severe allergic reaction"],
  ["scrotal_pain_young_male", "Sudden testicular pain (young male)"],
  ["severe_pain", "Severe pain"],
  ["sudden_abdominal_pain", "Sudden severe abdominal pain"],
  ["sudden_headache", "Sudden severe headache"],
  ["urinary_retention", "Cannot pass urine"],
  ["fever_immunocompromised", "Fever with weak immunity (e.g. on chemotherapy)"],
  ["outside_eval_time_sensitive", "Time-sensitive condition seen elsewhere"],
  ["syncope", "Fainted or lost consciousness"],
  ["needle_prick_injury", "Needle-prick injury"],
  ["abd_pain_with_vaginal_bleeding", "Abdominal pain with vaginal bleeding"],
  ["agitated_violent", "Agitated or violent"],
  ["poisoning_envenomation", "Poisoning or snake/insect bite"],
  ["third_trimester_pain_or_bleeding", "Pain or bleeding in the last 3 months of pregnancy"],
] as const;
export type AtpFlag = (typeof ATP_FLAGS)[number][0];
const FLAG_SET = new Set<string>(ATP_FLAGS.map(([k]) => k));

export const CONSCIOUSNESS = [
  ["A", "Alert"],
  ["C", "New confusion"],
  ["V", "Responds to voice"],
  ["P", "Responds to pain"],
  ["U", "Unresponsive"],
] as const;

export type FormState = {
  scenario: Scenario;
  age_years: string;
  pregnant: boolean;
  trimester: "" | "1" | "2" | "3";
  resp_rate: string;
  spo2: string;
  on_supplemental_oxygen: "" | "yes" | "no";
  pulse: string;
  sbp: string;
  dbp: string;
  temp_c: string;
  consciousness: "" | "A" | "C" | "V" | "P" | "U";
  red_flag_screen_completed: boolean;
  red_flags_present: string[];
  suspected_infection: boolean;
};

export type FieldKey = keyof FormState;

export function emptyForm(scenario: Scenario): FormState {
  return {
    scenario,
    age_years: "",
    pregnant: scenario === "maternal",
    trimester: "",
    resp_rate: "",
    spo2: "",
    on_supplemental_oxygen: "",
    pulse: "",
    sbp: "",
    dbp: "",
    temp_c: "",
    consciousness: "",
    red_flag_screen_completed: false, // only a human ticks this; never pre-filled
    red_flags_present: [],
    suspected_infection: false,
  };
}

// Backend ranges (Vitals / TriageInput fields).
const INT_FIELDS: { key: FieldKey; label: string; min: number; max: number }[] = [
  { key: "age_years", label: "Age", min: 0, max: 120 },
  { key: "resp_rate", label: "Breathing rate", min: 0, max: 80 },
  { key: "spo2", label: "Oxygen saturation (SpO2)", min: 0, max: 100 },
  { key: "pulse", label: "Pulse", min: 0, max: 300 },
  { key: "sbp", label: "Systolic blood pressure", min: 0, max: 300 },
  { key: "dbp", label: "Diastolic blood pressure", min: 0, max: 200 },
];

export type TriageInput = {
  scenario: Scenario;
  age_years?: number;
  pregnant: boolean;
  trimester?: 1 | 2 | 3;
  vitals: Partial<{ resp_rate: number; spo2: number; on_supplemental_oxygen: boolean; pulse: number; sbp: number; dbp: number; temp_c: number; consciousness: string }>;
  red_flag_screen_completed: boolean;
  red_flags_present: AtpFlag[];
  suspected_infection: boolean;
};

export type BuildResult = { ok: true; input: TriageInput } | { ok: false; errors: Partial<Record<FieldKey, string>> };

function parseInt10(raw: string): number | null | "invalid" {
  const s = raw.trim();
  if (s === "") return null;
  if (!/^\d+$/.test(s)) return "invalid";
  return Number(s);
}

export function buildTriageInput(f: FormState): BuildResult {
  const errors: Partial<Record<FieldKey, string>> = {};
  const nums: Partial<Record<FieldKey, number>> = {};
  for (const { key, label, min, max } of INT_FIELDS) {
    const v = parseInt10(String(f[key]));
    if (v === "invalid") errors[key] = `${label} must be a whole number`;
    else if (v !== null && (v < min || v > max)) errors[key] = `${label} must be between ${min} and ${max}`;
    else if (v !== null) nums[key] = v;
  }
  let temp: number | undefined;
  const t = f.temp_c.trim();
  if (t !== "") {
    if (!/^\d{2}(\.\d{1,2})?$/.test(t)) errors.temp_c = "Temperature must be in °C, for example 38.5";
    else {
      const v = Number(t); // never rounded: rounding can move a value across a rule boundary
      if (v < 25 || v > 45) errors.temp_c = "Temperature must be between 25 and 45 °C";
      else temp = v;
    }
  }
  if (nums.sbp !== undefined && nums.dbp !== undefined && nums.dbp >= nums.sbp) errors.dbp = "Diastolic must be lower than systolic";
  if (!SCENARIOS.includes(f.scenario)) errors.scenario = "Unknown scenario";
  if (f.scenario === "maternal" && !f.pregnant) errors.pregnant = "The maternal scenario is for pregnant patients";
  if (f.trimester && !f.pregnant) errors.trimester = "Trimester applies only if pregnant";
  const unknownFlag = f.red_flags_present.find((x) => !FLAG_SET.has(x));
  if (unknownFlag) errors.red_flags_present = "Unknown red flag";
  if (f.red_flags_present.length > 0 && !f.red_flag_screen_completed) errors.red_flag_screen_completed = "Confirm the red-flag screen was done with the patient";
  if (Object.keys(errors).length > 0) return { ok: false, errors };

  const vitals: TriageInput["vitals"] = {};
  for (const k of ["resp_rate", "spo2", "pulse", "sbp", "dbp"] as const) if (nums[k] !== undefined) vitals[k] = nums[k];
  if (temp !== undefined) vitals.temp_c = temp;
  if (f.on_supplemental_oxygen) vitals.on_supplemental_oxygen = f.on_supplemental_oxygen === "yes";
  if (f.consciousness) vitals.consciousness = f.consciousness;
  const input: TriageInput = {
    scenario: f.scenario,
    pregnant: f.pregnant,
    vitals,
    red_flag_screen_completed: f.red_flag_screen_completed,
    red_flags_present: f.red_flags_present as AtpFlag[],
    suspected_infection: f.suspected_infection,
  };
  if (nums.age_years !== undefined) input.age_years = nums.age_years;
  if (f.trimester) input.trimester = Number(f.trimester) as 1 | 2 | 3;
  return { ok: true, input };
}

// Map a backend validation error (400 VALIDATION_ERROR, `details.errors[].field`, e.g. "vitals.spo2") back to a form field.
export function fieldForErrorPath(path: string): FieldKey | null {
  const last = path.split(".").filter((p) => p !== "body" && p !== "vitals" && !/^\d+$/.test(p)).pop() ?? "";
  const keys: FieldKey[] = ["age_years", "pregnant", "trimester", "resp_rate", "spo2", "on_supplemental_oxygen", "pulse", "sbp", "dbp", "temp_c", "consciousness", "red_flag_screen_completed", "red_flags_present", "suspected_infection", "scenario"];
  return (keys as string[]).includes(last) ? (last as FieldKey) : null;
}

// Where a pre-filled value came from. Only human-reviewed sources may pre-fill (docs/12, docs/14, docs/16).
export type PrefillSource = { kind: "voice" | "voice_corrected" | "ai_reviewed" | "ai_corrected"; detail: string };

// Only these numeric text fields can be pre-filled; flags, screens and scenario are always entered by a person.
export type PrefillKey = "age_years" | "spo2" | "pulse" | "resp_rate" | "sbp" | "dbp" | "temp_c";
export type Prefill = { values: Partial<Record<PrefillKey, string>>; sources: Partial<Record<FieldKey, PrefillSource[]>>; conflicts: FieldKey[] };

const VOICE_TO_FORM: Record<string, PrefillKey> = { temp_c: "temp_c", spo2: "spo2", pulse: "pulse", resp_rate: "resp_rate", sbp: "sbp", dbp: "dbp" };
const HINT_TO_FORM: Record<string, PrefillKey> = { "vitals.temp_c": "temp_c", "vitals.spo2": "spo2", "vitals.pulse": "pulse", "vitals.resp_rate": "resp_rate", "vitals.sbp": "sbp", "vitals.dbp": "dbp" };

export type VoicePrefill = {
  vitals: Record<string, number>;
  fields: { age_years?: number; pregnant?: boolean };
  values: Record<string, { values: Record<string, number | boolean>; sources: { type: string; resolved_by_role: string }[] }>;
};
export type AiReviewed = {
  values: { field: string; basis: string; reviewed_by_role?: string; form_hints: { form_field: string; value: unknown }[] }[];
  unresolved?: { field_id: string; field: string; status: string; priority_review: boolean; state: string }[];
  conflicting_readings?: string[];
};

// Merge human-confirmed voice values and human-reviewed AI form hints. Differing values for one field are
// never resolved silently: the field stays blank and is listed as a conflict. Red flags are never pre-ticked.
export function mergePrefill(voice: VoicePrefill | null, ai: AiReviewed | null): Prefill {
  const values: Partial<Record<PrefillKey, string>> = {};
  const sources: Partial<Record<FieldKey, PrefillSource[]>> = {};
  const conflicts = new Set<PrefillKey>();
  function put(key: PrefillKey, raw: unknown, src: PrefillSource) {
    if (typeof raw !== "number" || !Number.isFinite(raw)) return;
    const v = String(raw);
    if (values[key] !== undefined && values[key] !== v) conflicts.add(key);
    values[key] = values[key] ?? v;
    (sources[key] ??= []).push(src);
  }
  if (voice) {
    const voiceKind = (field: string): PrefillSource["kind"] =>
      voice.values[field]?.sources.some((s) => s.type === "voice_manual_correction") ? "voice_corrected" : "voice";
    const fieldOf: Record<string, string> = { temp_c: "temp", spo2: "spo2", pulse: "pulse", resp_rate: "resp_rate", sbp: "bp", dbp: "bp" };
    for (const [k, v] of Object.entries(voice.vitals)) {
      const key = VOICE_TO_FORM[k];
      if (key) put(key, v, { kind: voiceKind(fieldOf[k]), detail: "voice read-back, checked by a health worker" });
    }
    if (typeof voice.fields.age_years === "number") put("age_years", voice.fields.age_years, { kind: voiceKind("age"), detail: "voice read-back, checked by a health worker" });
  }
  if (ai) {
    for (const v of ai.values) {
      for (const h of v.form_hints) {
        const key = HINT_TO_FORM[h.form_field];
        if (key) put(key, h.value, { kind: v.basis === "reviewer_corrected" ? "ai_corrected" : "ai_reviewed", detail: "AI-extracted from text, reviewed by a health worker" });
      }
    }
  }
  // Blood pressure is one reading: if either half is disputed, neither half is filled in.
  if (conflicts.has("sbp") || conflicts.has("dbp")) {
    conflicts.add("sbp");
    conflicts.add("dbp");
  }
  for (const k of conflicts) delete values[k];
  return { values, sources, conflicts: [...conflicts] };
}
