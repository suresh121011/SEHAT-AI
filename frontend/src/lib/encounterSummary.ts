// View logic for the clinical encounter summary (backend app/ai/encounter_summary.py, docs/19). Pure functions only, so
// they are testable with node --test. Nothing here computes urgency or chooses between conflicting values: the page
// shows what the server assembled, in a fixed order, with "needs attention" first.

export type Trust = "checked" | "entered" | "unchecked";

export type SourceRef = {
  type: string;
  [k: string]: unknown;
};

export type Fact = {
  fact_id: string;
  section: string;
  key: string;
  label: string;
  display: string;
  value: unknown;
  provenance: string;
  trust: Trust;
  sources: SourceRef[];
  flags: string[];
};

export type Row = { key: string; label: string; fact_ids: string[]; display: string | null; conflict: boolean; history: { display: string; recorded_at: string }[] };
export type Attention = { kind: string; message: string; action: string; refs: string[] };
export type Missing = { key: string; label: string; why_it_matters: string; is_danger_sign: boolean; source: string };
export type Pending = { kind: string; ref: string; label: string; state: string; action: string };
export type Conflict = { key: string; label: string; fact_ids: string[]; values: { display: string; provenance: string; trust: Trust }[]; action: string };

export type EncounterSummary = {
  schema_version: string;
  case_id: string;
  scenario: string;
  status: "complete" | "partial";
  generated_at: string;
  attention: Attention[];
  presenting_problem: string[];
  timeline: string[];
  symptoms: { present: string[]; denied: string[] };
  vitals: Row[];
  labs: Row[];
  medications: string[];
  body_map: string[];
  red_flag_mentions: string[];
  context: string[];
  missing_information: Missing[];
  follow_up_questions: { field_name: string; question_text: string }[];
  conflicts: Conflict[];
  pending_review: Pending[];
  unavailable_sources: { source: string; reason: string }[];
  excluded_sources: { source: string; reason: string }[];
  urgency: {
    status: "rules_engine" | "not_determined";
    recorded_urgency: "RED" | "YELLOW" | "GREEN" | null;
    triage_run_at?: string;
    triggered_rules?: { rule_id: string; urgency: string; reason: string }[];
    review_status?: string;
    reviewer_urgency?: string | null;
    message: string;
  };
  narrative: { section: string; text: string; fact_ids: string[] }[];
  blocked: { section: string; reason: string; fact_ids: string[] }[];
  facts: Record<string, Fact>;
  trust_legend: Record<Trust, string>;
  disclaimer: string;
};

export type ReviewEvent = { event_id: string; outcome: "accepted" | "needs_changes"; acknowledged_conflicts: boolean; actor_role: string; created_at: string };

export type SummaryView = {
  case_id: string;
  summary_id?: string;
  created_at?: string;
  is_stale?: boolean;
  stale_message?: string | null;
  review?: { status: "draft_pending_review" | "accepted" | "needs_changes"; latest: ReviewEvent | null; history: ReviewEvent[] };
  summary: EncounterSummary | null;
  message?: string;
};

/** Three trust levels only (council: one small vocabulary, defined on the page). */
export const TRUST_WORDS: Record<Trust, { label: string; tone: "done" | "neutral" | "attention"; icon: "check" | "clipboard" | "question" }> = {
  checked: { label: "Checked by a person", tone: "done", icon: "check" },
  entered: { label: "Entered by staff", tone: "neutral", icon: "clipboard" },
  unchecked: { label: "Not yet checked", tone: "attention", icon: "question" },
};

/** Attention order: things that make the summary unsafe to rely on come first. */
const ATTENTION_ORDER = ["unavailable_source", "conflict", "missing_danger_sign", "no_triage_run", "triage_predates_evidence", "blocked_sentence", "pending_review"];

export function sortedAttention(items: Attention[]): Attention[] {
  const rank = (k: string) => {
    const i = ATTENTION_ORDER.indexOf(k);
    return i === -1 ? ATTENTION_ORDER.length : i;
  };
  return [...items].sort((a, b) => rank(a.kind) - rank(b.kind));
}

export function attentionTone(kind: string): "danger" | "attention" {
  return kind === "unavailable_source" || kind === "conflict" || kind === "missing_danger_sign" ? "danger" : "attention";
}

/** Plain words for where a fact came from. Never invents a location the source did not record. */
export function sourceWords(src: SourceRef): string {
  const role = typeof src.resolved_by_role === "string" ? ` · checked by ${roleWord(src.resolved_by_role)}` : "";
  switch (src.type) {
    case "voice_transcript":
      return `Voice recording, transcript characters ${charSpan(src.transcript_chars)}${role}`;
    case "voice_manual_correction":
      return `Voice recording, value typed by the reviewer${role}`;
    case "ocr":
      return `${docWord(src.document_type)}, page ${Number(src.page_index ?? 0) + 1} (highlighted region)${role}`;
    case "ocr_manual_correction":
      return `${docWord(src.document_type)}, page ${Number(src.page_index ?? 0) + 1}, typed by the reviewer from the paper${role}`;
    case "triage_form":
      return `Triage form (${String(src.field ?? "value")})`;
    case "ai_field": {
      const ev = Array.isArray(src.evidence) ? (src.evidence as { quote?: string }[]) : [];
      const quote = ev.find((e) => e.quote)?.quote;
      return quote ? `Intake text or transcript: “${quote}”` : "Intake text or transcript (AI-extracted)";
    }
    case "body_map":
      return `Body map${typeof src.recorded_by_role === "string" ? `, marked by ${roleWord(src.recorded_by_role)}` : ""}`;
    default:
      return "Source recorded (type not shown)";
  }
}

function charSpan(v: unknown): string {
  return Array.isArray(v) && v.length === 2 ? `${v[0]}–${v[1]}` : "(not recorded)";
}

function docWord(t: unknown): string {
  return t === "prescription" ? "Prescription" : t === "discharge_summary" ? "Discharge summary" : "Lab report";
}

export function roleWord(r: string): string {
  return { anm: "health worker", medical_officer: "medical officer", patient: "patient", supervisor: "supervisor" }[r] ?? r;
}

export function reviewWords(status: string | undefined): { text: string; tone: "done" | "neutral" | "attention" } {
  if (status === "accepted") return { text: "Summary accepted by a reviewer (not a diagnosis)", tone: "done" };
  if (status === "needs_changes") return { text: "Reviewer marked: needs changes", tone: "attention" };
  return { text: "Draft — waiting for review", tone: "neutral" };
}

/** Whether "Accept" may be offered, and why not. Mirrors the server rules; the server still decides. */
export function acceptState(v: SummaryView): { allowed: boolean; needsConflictAck: boolean; reason: string | null } {
  const s = v.summary;
  if (!s) return { allowed: false, needsConflictAck: false, reason: "No summary yet." };
  if (v.is_stale) return { allowed: false, needsConflictAck: false, reason: "Information changed after this summary was made. Refresh it first." };
  if (s.status !== "complete") return { allowed: false, needsConflictAck: false, reason: "A source could not be read, so this summary is incomplete." };
  return { allowed: true, needsConflictAck: s.conflicts.length > 0, reason: null };
}

/** Facts for a list of ids, skipping ids the server did not send (never fabricates a row). */
export function factsFor(s: EncounterSummary, ids: string[]): Fact[] {
  return ids.map((i) => s.facts[i]).filter((f): f is Fact => Boolean(f));
}

export function flagWords(flag: string): string | null {
  if (flag === "uncertain_handwriting") return "Handwriting uncertain — confirm against the paper; do not act on this reading";
  if (flag === "disputed_reading") return "Two machine readings differ";
  if (flag.startsWith("drug_name_not_matched")) return "Drug name not matched in the offline drug list";
  if (flag.startsWith("band:")) return null; // engine band is shown by the document view
  if (flag.startsWith("linked_to:")) return null;
  return flag.replaceAll("_", " ");
}

const SOURCE_NAMES: Record<string, string> = { ai_extraction: "AI extraction", voice: "voice read-back", documents: "documents", triage: "triage form", body_map: "body map", triage_input: "triage input" };

export function sourceName(s: string): string {
  return SOURCE_NAMES[s] ?? s.replaceAll("_", " ");
}
