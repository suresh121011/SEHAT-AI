// View logic for "Check these first" (backend app/verification_queue.py, GET cases/{id}/verification-queue). Pure functions
// only, tested with node --test. The queue is an order for checking: nothing here confirms a value or changes urgency.

export type Priority = "match_document" | "could_change_urgency" | "abnormal_lab" | "routine";

export type QueueItem = {
  item_id: string;
  kind: "document_attestation" | "document_field" | "voice_value";
  priority: Priority;
  reasons: string[];
  label: string;
  shown: string | null;
  ref: { document_id?: string; field_id?: string; candidate_id?: string };
  act_on: "documents" | "voice";
  can_confirm: boolean | null;
  urgency_if_confirmed: { now: string; if_confirmed: string; hypothetical: true } | null;
  field?: string;
};

export type Queue = { case_id: string; items: QueueItem[]; counts: Record<Priority | "total", number>; note: string; clinical_use_allowed: false };

export const PRIORITY_WORDS: Record<Priority, { title: string; tone: "error" | "warning" | "info" }> = {
  match_document: { title: "Match this document to the patient first", tone: "warning" },
  could_change_urgency: { title: "Check these first: they could make this patient urgent", tone: "error" },
  abnormal_lab: { title: "Abnormal lab values to check", tone: "warning" },
  routine: { title: "Still to check", tone: "info" },
};

const REASONS: Record<string, string> = {
  not_answered: "nobody has said whether this report is the patient's",
  answered_does_not_match: "marked as not this patient's report",
  answered_unsure: "marked not sure whose report this is",
  printed_flag_high: "printed H (high) on the report",
  printed_flag_low: "printed L (low) on the report",
  printed_flag_abnormal: "marked abnormal on the report",
  outside_printed_range: "outside the range printed on the report",
  outside_reference_range: "outside the sourced reference range",
  marked_not_sure: "someone marked it not sure",
  readings_differ: "the two readings differ",
  could_not_be_read: "could not be read",
  needs_correction: "type it from the source; it cannot be confirmed as read",
  document_not_matched_to_patient: "match the document to the patient first",
  would_change_rules_urgency: "the rules would give a different urgency",
  no_triage_run_to_compare: "no triage run yet to compare with",
  could_not_compare: "could not be compared with the triage form",
};

export function reasonWords(codes: string[]): string[] {
  return codes.map((c) => REASONS[c] ?? c.replaceAll("_", " "));
}

/** Plain sentence for the hypothetical urgency, or null when the engine gave none or nothing would change. */
export function urgencyWords(u: QueueItem["urgency_if_confirmed"]): string | null {
  if (!u || u.now === u.if_confirmed) return null;
  return `If this value is right and is entered on the triage form, the rules would give ${u.if_confirmed} (now ${u.now}). Hypothetical — not recorded.`;
}

/** The highest-priority document row still in the queue, other than the one just decided. */
export function nextDocumentField(items: QueueItem[], decidedFieldId: string): { doc: string; field: string } | null {
  const next = items.find((i) => i.kind === "document_field" && i.ref.field_id && i.ref.field_id !== decidedFieldId);
  return next && next.ref.document_id && next.ref.field_id ? { doc: next.ref.document_id, field: next.ref.field_id } : null;
}

/** Keyboard decision for the review panel: C confirm, U not sure, R reject. Letters only, never Enter (Enter already
 * presses whichever button has focus, e.g. "Delete this document"). Null while typing in a box, with Ctrl/Cmd/Alt held
 * (Cmd+R reloads the page), or when the key means nothing here. */
export function keyAction(key: string, targetTag: string, canConfirm: boolean, modifier = false): "confirmed" | "unsure" | "rejected" | null {
  if (modifier || ["INPUT", "SELECT", "TEXTAREA"].includes(targetTag)) return null;
  const k = key.toLowerCase();
  if (k === "c") return canConfirm ? "confirmed" : null;
  return k === "u" ? "unsure" : k === "r" ? "rejected" : null;
}
