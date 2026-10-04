// Phase 8 reviewer dashboard: API types and pure display helpers (no React, no DOM; unit-tested with `node --test`).
// The backend (app/review_queue.py) is authoritative for urgency, queue order, escalation state and governance
// numbers. Nothing here sorts cases, computes urgency or decides whether an escalation happened: the helpers only
// filter in server order, format server values, and derive a display countdown from a server deadline.

export type Urgency = "RED" | "YELLOW" | "GREEN";
export type Determination = "complete" | "insufficient_data" | "outside_validated_population";
export type EscalationState = "pending" | "overdue" | "acknowledged";

export type Escalation = {
  state: EscalationState;
  anchor: string;
  anchor_source: string;
  deadline: string;
  window_seconds: number;
  acknowledged_at: string | null;
  acknowledged_by_role: string | null;
  acknowledged_via: "acknowledge" | "sign_off" | null;
  acknowledged_late: boolean | null;
  notification_sent: false;
};

export type QueueItem = {
  case_id: string;
  patient_token: string;
  facility_code: string;
  scenario: string;
  triage_run_id: string;
  triaged_at: string;
  waiting_seconds: number;
  determination: Determination | null;
  needs_human_review: boolean;
  missing_fields: string[];
  triggered_rule_ids: string[];
  consent_triage: string;
  rules_urgency: Urgency;
  effective_urgency: Urgency;
  effective_urgency_source: "rules_engine" | "reviewer_override";
  priority_urgency: Urgency;
  /** "open_red_escalation": kept at RED in the queue until the case is signed off; acknowledging alone does not lower it (server). */
  priority_source: "rules_engine" | "reviewer_override_raise" | "open_red_escalation";
  override_count: number;
  review_status: "awaiting_review" | "signed_off";
  signed_off_at: string | null;
  escalation: Escalation | null;
};

export type Queue = {
  generated_at: string;
  facility_code: string | null;
  ordering: string;
  escalation_window_seconds: number;
  counts: Record<Urgency, number>;
  items: QueueItem[];
};

export type TriggeredRule = { rule_id: string; urgency: string; reason: string; source: string; evidence?: Record<string, unknown> };
export type TriageResultFull = {
  urgency: Urgency;
  determination: Determination;
  needs_human_review: boolean;
  triggered_rules: TriggeredRule[];
  missing_fields: string[];
  advisories: { code: string; message: string }[];
  engine_version: string;
  ruleset_version: string;
  disclaimer: string;
};

export type ReviewEvent = {
  event_id: string;
  triage_run_id: string;
  kind: "sign_off" | "override" | "acknowledge";
  rules_urgency: Urgency;
  old_urgency: Urgency | null;
  new_urgency: Urgency | null;
  reason_code: string | null;
  reason_label: string | null;
  reason_text: string | null;
  actor_role: string;
  is_current_user: boolean;
  created_at: string;
};

export type AuditRow = { seq: number; timestamp: string; actor_role: string; action: string; outcome: string };
export type ReasonOption = { code: string; label: string; requires_text: boolean };

export type CorrectionReasonCode = "remeasured" | "entry_error" | "source_disputed" | "other";

/** A reviewer correction: a NEW rules-engine run made from corrected input (POST /triage/{id}/corrections). */
export type Correction = {
  triage_run_id: string;
  corrects_run_id: string;
  /** Null only if the corrected run cannot be read back (the server never guesses it). */
  urgency_before: Urgency | null;
  urgency_after: Urgency;
  changed_fields: string[];
  /** Old and new values; null when triage consent is not in effect (values hidden, field names still shown). */
  changes: { field: string; old: unknown; new: unknown }[] | null;
  reason_code: string;
  reason_label: string | null;
  reason_text: string | null;
  actor_role: string;
  is_current_user: boolean;
  created_at: string;
};

export type CorrectionResponse = {
  case_id: string;
  corrects_run_id: string;
  triage_run_id: string;
  urgency_before: Urgency;
  urgency: Urgency;
  changed_fields: string[];
  reason_code: string;
  corrected_at: string;
  reviewer_role: string;
};

export type FieldProvenanceEntry =
  | {
      source: "reviewer_correction";
      previous_value: unknown;
      from_run_id: string;
      created_at: string;
      actor_role: string;
      is_current_user: boolean;
      reason_code: string;
      reason_label: string | null;
    }
  | { source: "unchanged_from_previous_run"; from_run_id: string };

/** null = hidden (no consent); {} = not a correction run, so no per-field source is stored; else changed path →
 * entry, plus "*" = every other field was carried over unchanged from `from_run_id`. */
export type FieldProvenance = Record<string, FieldProvenanceEntry> | null;
export type ConsentStates = Record<string, string>;

export type CaseReview = {
  generated_at: string;
  case: { case_id: string; patient_token: string; scenario: string; facility_code: string; status: string; created_at: string };
  consent: ConsentStates;
  clinical_content_available: boolean;
  can_review: boolean;
  override_reasons: ReasonOption[];
  escalation_window_seconds: number;
  audit: AuditRow[];
  latest: (QueueItem & { result: TriageResultFull | null; input?: Record<string, unknown> | null; has_input: boolean; field_provenance?: FieldProvenance }) | null;
  runs: {
    run_id: string;
    urgency: Urgency;
    engine_version: string;
    ruleset_version: string;
    created_at: string;
    has_input: boolean | number;
    corrects_run_id?: string | null;
    correction_reason_code?: string | null;
  }[];
  review_events: ReviewEvent[];
  correction_reasons?: ReasonOption[];
  corrections?: Correction[];
};

export type Governance = {
  generated_at: string;
  period: { since: string | null; until: string | null; basis: string };
  sample_size: number;
  small_sample_warning: boolean;
  review_completion: { signed_off: number; denominator: number; rate: number | null; definition: string };
  overrides: {
    cases_with_override: number;
    denominator: number;
    rate: number | null;
    definition: string;
    events: number;
    raised: number;
    lowered: number;
    reasons: { code: string; label: string; count: number }[];
    interpretation: string;
  };
  red_escalation: {
    red_cases: number;
    pending: number;
    overdue_unacknowledged: number;
    acknowledged: number;
    acknowledged_within_window: number;
    acknowledged_late: number;
    window_seconds: number;
    notification_channel: null | string;
    /** Episodes across all runs of the period's cases (hardening pass); absent on older servers. */
    episodes?: number;
    definition?: string;
  };
  turnaround_seconds: { n: number; median: number | null; definition: string };
  insufficient_data: { count: number; denominator: number; rate: number | null };
  ai_field_reviews: { total: number; by_outcome: Record<string, number>; disagreement_rate: number | null; definition: string };
};

// ── Clock and countdown ──────────────────────────────────────────────────

/** Server clock minus client clock (ms), estimated at the moment a response arrived. */
export function clockOffsetMs(serverIso: string, receivedAtMs: number): number {
  const server = Date.parse(serverIso);
  return Number.isFinite(server) ? server - receivedAtMs : 0;
}

/** Time left until a server deadline, on the server's clock. Negative = past the deadline. */
export function remainingMs(deadlineIso: string, clientNowMs: number, offsetMs: number): number {
  return Date.parse(deadlineIso) - (clientNowMs + offsetMs);
}

/** "2:05", "0:00"; always non-negative (the caller says "left" or "over"). */
export function formatClock(ms: number): string {
  const total = Math.floor(Math.abs(ms) / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** Waiting time in words ("4 min", "2 h 5 min"); operational time, never clinical priority. */
export function formatWaiting(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "unknown";
  if (seconds < 60) return "under 1 min";
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} days`;
}

export type EscalationView = {
  /** What to show. "past_target" = the server said pending, but the deadline has passed on the corrected clock and
   * the next refresh has not confirmed it yet. Shown as overdue, labelled as awaiting server confirmation. */
  display: "pending" | "past_target" | "overdue" | "acknowledged";
  remainingMs: number;
  serverConfirmed: boolean;
};

export function escalationView(esc: Escalation, clientNowMs: number, offsetMs: number): EscalationView {
  const left = remainingMs(esc.deadline, clientNowMs, offsetMs);
  if (esc.state === "acknowledged") return { display: "acknowledged", remainingMs: left, serverConfirmed: true };
  if (esc.state === "overdue") return { display: "overdue", remainingMs: left, serverConfirmed: true };
  return left <= 0 ? { display: "past_target", remainingMs: left, serverConfirmed: false } : { display: "pending", remainingMs: left, serverConfirmed: true };
}

/** Data older than this many ms (on the server clock) is flagged as stale on screen. */
export const STALE_AFTER_MS = 45_000;
export const POLL_MS = 15_000;

export function isStale(generatedAtIso: string, clientNowMs: number, offsetMs: number, maxAgeMs = STALE_AFTER_MS): boolean {
  const t = Date.parse(generatedAtIso);
  return !Number.isFinite(t) || clientNowMs + offsetMs - t > maxAgeMs;
}

// ── Queue (server order is preserved; filtering never re-sorts) ──────────

export type QueueFilter = "all" | Urgency | "escalations";

export function filterQueue(items: QueueItem[], filter: QueueFilter): QueueItem[] {
  if (filter === "all") return items;
  if (filter === "escalations") return items.filter((i) => i.escalation !== null && i.escalation.state !== "acknowledged");
  return items.filter((i) => i.priority_urgency === filter);
}

/** Unacknowledged RED escalations in server order. Never filtered by the queue tab: the banner always shows them. */
export function openEscalations(items: QueueItem[]): QueueItem[] {
  return items.filter((i) => i.escalation !== null && i.escalation.state !== "acknowledged");
}

/** Rank used only to CHECK the server's order (tests, dev warning) and to word whether a server result went down; never used to sort. */
const RANK: Record<Urgency, number> = { RED: 3, YELLOW: 2, GREEN: 1 };
export function serverOrderLooksValid(items: QueueItem[]): boolean {
  for (let i = 1; i < items.length; i++) {
    const a = items[i - 1];
    const b = items[i];
    if (RANK[a.priority_urgency] < RANK[b.priority_urgency]) return false;
    if (a.priority_urgency === b.priority_urgency && a.triaged_at > b.triaged_at) return false;
  }
  return true;
}

// ── Words ────────────────────────────────────────────────────────────────

const FIELD_WORDS: Record<string, string> = {
  "vitals.resp_rate": "breathing rate",
  "vitals.spo2": "oxygen saturation (SpO2)",
  "vitals.pulse": "pulse",
  "vitals.sbp": "systolic blood pressure",
  "vitals.dbp": "diastolic blood pressure",
  "vitals.temp_c": "temperature",
  "vitals.consciousness": "level of alertness (ACVPU)",
  "vitals.on_supplemental_oxygen": "whether on oxygen",
  red_flag_screen_completed: "red-flag screen",
  red_flags_present: "red flags",
  age_years: "age",
};

export function fieldWords(field: string): string {
  return FIELD_WORDS[field] ?? field.replace(/^vitals\./, "").replaceAll("_", " ");
}

export type CounterfactualChange = { field?: string; remove?: string; from?: unknown; to?: unknown };

/** The hypothetical change in words. The value pair comes from the server; nothing is computed here. */
export function describeChange(c: CounterfactualChange): string {
  const field = String(c.field ?? "");
  if (field === "red_flags_present") return `If the red flag “${String(c.remove).replaceAll("_", " ")}” had not been recorded`;
  if (field === "red_flag_screen_completed") return "If the red-flag screen had not been completed";
  return `If ${fieldWords(field)} were ${String(c.to)} instead of ${String(c.from)}`;
}

const ROLE_WORDS: Record<string, string> = { anm: "Health worker (ANM)", medical_officer: "Medical officer", patient: "Patient account", supervisor: "Supervisor", admin: "Admin" };
export function roleLabel(role: string | null | undefined): string {
  return role ? (ROLE_WORDS[role] ?? role) : "Unknown role";
}

export const SCENARIO_WORDS: Record<string, string> = {
  opd: "OPD",
  maternal: "Maternal",
  chronic_ncd: "Chronic NCD",
  health_camp: "Health camp",
  campus_fever: "Campus fever",
  occupational: "Occupational",
  referral: "Referral",
};

// ── Governance (null = no data, never shown as zero) ─────────────────────

export function formatRate(rate: number | null, num: number, den: number): string {
  if (rate === null || den === 0) return `No data (0 cases in period)`;
  return `${(rate * 100).toFixed(1)}% (${num} of ${den})`;
}

export function formatSeconds(s: number | null): string {
  if (s === null) return "No data";
  return s < 120 ? `${Math.round(s)} s` : formatWaiting(s);
}

/** Bar width as a share of the largest count; 0 when every count is 0 (an empty chart, not an even split). */
export function barShare(count: number, counts: number[]): number {
  const max = Math.max(0, ...counts);
  return max === 0 ? 0 : count / max;
}

// ── Correcting recorded vitals (a NEW rules-engine run; the earlier run stays in history) ──

export const EDITABLE_VITALS = [
  { key: "spo2", label: "Oxygen saturation (SpO2, %)", min: 0, max: 100, decimal: false },
  { key: "pulse", label: "Pulse (beats/min)", min: 0, max: 300, decimal: false },
  { key: "resp_rate", label: "Breathing rate (/min)", min: 0, max: 80, decimal: false },
  { key: "sbp", label: "Systolic BP (mmHg)", min: 0, max: 300, decimal: false },
  { key: "dbp", label: "Diastolic BP (mmHg)", min: 0, max: 200, decimal: false },
  { key: "temp_c", label: "Temperature (°C)", min: 25, max: 45, decimal: true },
] as const;
export type EditableVital = (typeof EDITABLE_VITALS)[number]["key"];
export type VitalEdits = Partial<Record<EditableVital, string>>;

export type PatchResult = { ok: true; input: Record<string, unknown>; changed: EditableVital[] } | { ok: false; errors: Partial<Record<EditableVital, string>> };

/** Apply edited vitals to the stored rules-engine input. Every other field (scenario blocks, red flags, screen,
 * age, consciousness) is sent back exactly as recorded. Blank = value not recorded (missing, never "normal").
 * Ranges mirror backend Vitals; the backend validates again and stays authoritative. */
export function applyVitalEdits(input: Record<string, unknown>, edits: VitalEdits): PatchResult {
  const vitals = { ...((input.vitals as Record<string, unknown> | undefined) ?? {}) };
  const errors: Partial<Record<EditableVital, string>> = {};
  const changed: EditableVital[] = [];
  for (const { key, label, min, max, decimal } of EDITABLE_VITALS) {
    if (!(key in edits)) continue;
    const raw = (edits[key] ?? "").trim();
    let next: number | null = null;
    if (raw !== "") {
      const ok = decimal ? /^\d{2}(\.\d{1,2})?$/.test(raw) : /^\d+$/.test(raw);
      if (!ok) {
        errors[key] = decimal ? `${label}: use °C, for example 38.5` : `${label}: whole number`;
        continue;
      }
      next = Number(raw); // never rounded: rounding can move a value across a rule boundary
      if (next < min || next > max) {
        errors[key] = `${label}: between ${min} and ${max}`;
        continue;
      }
    }
    const prev = vitals[key] ?? null;
    if (prev !== next) changed.push(key);
    if (next === null) delete vitals[key];
    else vitals[key] = next;
  }
  const s = vitals.sbp, d = vitals.dbp;
  if (typeof s === "number" && typeof d === "number" && d >= s) errors.dbp = "Diastolic must be lower than systolic";
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, input: { ...input, vitals }, changed };
}

// ── Queue wording (server values only; nothing here decides priority) ──

/** The missing-data pill. Never "0 missing": with consent withdrawn the server hides the list but keeps the
 * determination, and an empty list is not evidence that nothing is missing. */
export function missingLabel(item: Pick<QueueItem, "determination" | "missing_fields" | "consent_triage">): string | null {
  if (item.determination !== "insufficient_data") return null;
  if (item.missing_fields.length > 0) return `${item.missing_fields.length} missing`;
  return item.consent_triage !== "granted" ? "Needs information (details hidden)" : "Needs information";
}

/** Why the queue position differs from the latest rules result, in words; null when it does not. */
export function priorityExplanation(item: Pick<QueueItem, "priority_source" | "priority_urgency" | "rules_urgency" | "effective_urgency">): string | null {
  if (item.priority_source === "open_red_escalation") return `Stays RED in the queue until a medical officer signs it off, because an earlier RED was raised (latest rules result: ${item.rules_urgency}). Acknowledging alone does not lower it.`;
  if (item.priority_source === "reviewer_override_raise") return `Raised by reviewer to ${item.priority_urgency} (rules: ${item.rules_urgency}).`;
  if (item.effective_urgency !== item.priority_urgency) return `A reviewer recorded ${item.effective_urgency}, but the case stays at ${item.priority_urgency} in the queue because that is the rules result — lowering never moves a case down the list.`;
  return null;
}

// ── Corrections and per-field provenance (server values only) ──

/** A recorded value in words; null/undefined = "not recorded" (never "normal"). */
export function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "not recorded";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.length ? v.map((x) => String(x).replaceAll("_", " ")).join(", ") : "none";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const shortRun = (id: string) => id.slice(0, 8);
const hhmm = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown time" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

export type ProvenanceNote = { kind: "corrected" | "unchanged"; text: string };

/** What the server says about one recorded field of the latest run. null = nothing stored (say so elsewhere). */
export function provenanceNote(path: string, fp: FieldProvenance | undefined): ProvenanceNote | null {
  if (!fp) return null;
  const e = fp[path];
  if (e && e.source === "reviewer_correction") {
    const who = `${roleLabel(e.actor_role)}${e.is_current_user ? " (you)" : ""}`;
    const why = e.reason_label ?? e.reason_code;
    return { kind: "corrected", text: `Changed by ${who} at ${hhmm(e.created_at)} — ${why}; was ${formatValue(e.previous_value)}` };
  }
  const star = fp["*"];
  if (star && star.source === "unchanged_from_previous_run") return { kind: "unchanged", text: "Unchanged since the earlier check" };
  return null;
}

/** One history line for a correction. Values appear only when the server sent them (`changes` non-null). */
export function correctionLine(c: Correction): string {
  const reason = c.reason_label ?? c.reason_code;
  const changed = c.changes
    ? c.changes.map((x) => `${fieldWords(x.field)} (${formatValue(x.old)} → ${formatValue(x.new)})`)
    : c.changed_fields.map(fieldWords);
  return `Correction: run ${shortRun(c.corrects_run_id)} → run ${shortRun(c.triage_run_id)}, ${reason}; changed: ${changed.length ? changed.join(", ") : "nothing listed"}`;
}

/** Success text built only from the server's response. */
export function correctionSuccess(r: CorrectionResponse): { message: string; lowered: boolean } {
  const lowered = RANK[r.urgency] < RANK[r.urgency_before];
  const fields = r.changed_fields.map(fieldWords).join(", ") || "none listed";
  const base = `Correction recorded at ${hhmm(r.corrected_at)} (server time) as a new rules-engine run (run ${shortRun(r.triage_run_id)}, correcting run ${shortRun(r.corrects_run_id)}). Rules result ${r.urgency_before} → ${r.urgency}. Changed: ${fields}. The earlier run and its review stay in the history.`;
  return { message: lowered ? `${base} The new rules result is lower; the case needs a fresh sign-off.` : `${base} Review restarts on the new run.`, lowered };
}

/** Server body path → correct-vitals dialog control key (null = summary only). */
export function correctionFieldControl(field: string): string | null {
  if (field === "reason_code" || field === "reason_text" || field === "confirm") return field;
  const m = /^input\.vitals\.([a-z_0-9]+)$/.exec(field);
  if (m && EDITABLE_VITALS.some((v) => v.key === m[1])) return m[1];
  return null;
}

/** Server body path → override dialog control key (null = summary only). */
export function overrideFieldControl(field: string): string | null {
  return field === "new_urgency" || field === "reason_code" || field === "reason_text" || field === "confirm" ? field : null;
}

// ── Voice evidence wording (field keys from backend app/voice/readback.py PREFILL_FIELDS) ──

const VOICE_FIELD_WORDS: Record<string, string> = {
  temp: "temperature",
  spo2: "oxygen saturation (SpO2)",
  pulse: "pulse",
  resp_rate: "breathing rate",
  bp: "blood pressure",
  age: "age",
  pregnancy: "pregnancy",
  unassigned: "a number not yet assigned to a field",
};

export function voiceFieldWords(field: string): string {
  return VOICE_FIELD_WORDS[field] ?? fieldWords(field);
}

export function voiceSourceWords(type: string): string {
  return type === "voice_manual_correction" ? "Voice, corrected by a person" : type === "voice_transcript" ? "Voice-transcribed, confirmed by a person" : "Voice (source type not recognised)";
}
