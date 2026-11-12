// The fenced clinical assistant (docs/24): types and plain-word helpers for the panel. No imports, so `node --test` runs
// it directly. The server's gate decides what may be shown; this only words it.

export type AssistantCite = { id: string; kind: "fact" | "missing" | "conflict" | "waiting" | "question" | "change"; label: string };
export type AssistantAnswer = {
  run_id: string;
  question_id: string;
  question: string;
  status: "answered" | "unparseable" | "tool_limit";
  reused: boolean;
  created_at: string;
  model: string;
  prompt_version: string;
  gate_version: string;
  sentences: { text: string; cites: AssistantCite[] }[];
  dropped: { reason: string; cites: string[] }[];
  tools: { called: string[]; fetched_by_server: string[]; refused: string[] };
  tokens: { prompt: number | null; completion: number | null };
  is_stale: boolean;
  notice: string;
};
export type AssistantView = {
  available: boolean;
  reason: string | null;
  model: string | null;
  questions: { id: string; text: string }[];
  answers: AssistantAnswer[];
  consent_needed: boolean;
  limits: { max_tool_calls: number; max_sentences: number; timeout_s: number };
  notice: string;
};

const UNAVAILABLE: Record<string, string> = {
  cloud_disabled: "cloud models are switched off on this server",
  synthetic_only_off: "the synthetic-data-only switch is off",
  credentials_incomplete: "the Azure settings are incomplete",
  endpoint_not_allowed: "the Azure address is not on the allowed list",
};

export function unavailableWords(reason: string | null): string {
  return (reason && UNAVAILABLE[reason]) || "the cloud model is not set up";
}

export const DROP_WORDS: Record<string, string> = {
  uncited_or_unknown_item: "cited nothing, or something the tools did not return",
  number_not_in_cited_items: "a number not in what it cited",
  missing_unchecked_tag: "an unchecked value without its tag",
  negation_or_time_uncertain: "a value whose negation or timing is unclear",
  polarity_mismatch: "a denied item stated as reported, or the reverse",
  unchecked_document_value_in_prose: "a machine-read document value",
  urgency_statement: "a statement about urgency",
  judgement_word: "a judgement such as normal, mild or safe",
  number_in_words: "a number written in words",
  unexpected_bracket: "a bracketed note that is not an unchecked tag",
  removed_value_as_current: "a removed value stated as current",
  unexpected_character: "an invisible or look-alike character",
  uncited_item_named: "an item named without citing it",
  cited_item_not_named: "a cited item it did not name",
  item_form_mismatch: "not in the fixed form for a missing, unchecked, conflicting or follow-up item",
  check_state_mismatch: "a check that was not made",
  change_contradicted: "a change stated as no change",
  too_many_citations: "too many citations",
  urgency_lowering_language: "wording that lowers urgency",
  diagnostic_language: "diagnostic wording",
  prescriptive_language: "treatment or dose wording",
  instruction_text: "instructions to a model",
  pii_pattern_in_output: "an identifier",
  review_bypass_or_override: "wording to skip review or override a rule",
  safety_override_attempt: "wording to override a safety rule",
  diagnosis_or_prescription_request: "a request for a diagnosis or prescription",
  too_long: "too long",
  too_many_sentences: "more sentences than allowed",
  empty: "empty",
};

/** "2 sentences dropped: diagnostic wording (1), a statement about urgency (1)." or null when none was dropped. */
export function droppedLine(dropped: { reason: string }[]): string | null {
  if (dropped.length === 0) return null;
  const counts = new Map<string, number>();
  for (const d of dropped) counts.set(d.reason, (counts.get(d.reason) ?? 0) + 1);
  const parts = [...counts].map(([r, n]) => `${DROP_WORDS[r] ?? r.replaceAll("_", " ")} (${n})`);
  return `${dropped.length} ${dropped.length === 1 ? "sentence" : "sentences"} dropped: ${parts.join(", ")}.`;
}

const TOOL_WORDS: Record<string, string> = {
  get_visit_facts: "visit facts",
  list_missing_information: "missing information",
  list_conflicts: "conflicting sources",
  list_waiting_for_check: "values waiting for a check",
  get_urgency_record: "rules engine record",
  suggest_follow_up_questions: "follow-up questions",
  list_changes_since_last_summary: "changes since the last summary",
};

export function toolLine(tools: { called: string[]; fetched_by_server?: string[]; refused: string[] }): string {
  const words = (ts: string[]) => ts.map((t) => TOOL_WORDS[t] ?? t);
  const fetched = tools.fetched_by_server ?? [];
  // The readable draft: the server fetched the facts and the model chose no tool. Say so, not "the model called".
  const head = fetched.length && !tools.called.length
    ? `Fetched by the server (the model chose no tool): ${words(fetched).join(", ")}.`
    : `Tools the model called: ${tools.called.length ? words(tools.called).join(" → ") : "none"}.`;
  const refused = tools.refused.length ? ` Refused (not on this question's list): ${words(tools.refused).join(", ")}.` : "";
  return head + refused;
}

export function statusWords(a: Pick<AssistantAnswer, "status">, maxToolCalls: number): string | null {
  if (a.status === "unparseable") return "The model's reply was not in the agreed form, so nothing from it is shown.";
  if (a.status === "tool_limit") return `The model asked for more than ${maxToolCalls} tool calls, so the run was stopped and nothing is shown.`;
  return null;
}
