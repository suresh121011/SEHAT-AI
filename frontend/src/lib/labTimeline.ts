// Lab timeline (docs/22): types and pure wording helpers for the case workspace panel. No imports, so `node --test`
// runs it directly. The server decides what is shown (checked values only) and what is compared; this only words it.

export type TimelineFlag = { code: string; text: string };
export type TimelineResult = {
  date: string;
  date_source: "report_date" | "collected_date" | "visit_date";
  /** The date was read by the machine from the report and nobody has checked it. */
  date_machine_read: boolean;
  date_regions: unknown[];
  value: string | null;
  comparator: string | null;
  qualitative: string | null;
  unit: string | null;
  shown: string;
  outcome: "confirmed" | "corrected";
  flags: TimelineFlag[];
  change: { direction?: "higher" | "lower" | "same"; not_compared?: string; text: string } | null;
  case_id: string;
  visit_date: string;
  current_visit: boolean;
  source: { type: string; document_id: string; field_id: string; page_index: number };
};
export type TimelineTest = { analyte: string; label: string; results: TimelineResult[]; flags: TimelineFlag[] };
export type LabTimeline = {
  case_id: string;
  generated_at: string;
  linked: boolean;
  other_visits: "shown" | "not_linked" | "consent_not_in_effect";
  other_visits_text: string | null;
  visits: { case_id: string; visit_date: string; current: boolean }[];
  tests: TimelineTest[];
  gaps: { code: string; count: number; text: string }[];
  note: string;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const DATE_SOURCE_WORDS: Record<TimelineResult["date_source"], string> = {
  report_date: "report date",
  collected_date: "sample collection date",
  visit_date: "visit date (no readable date on the report)",
};

export const OUTCOME_WORDS: Record<TimelineResult["outcome"], string> = {
  confirmed: "machine reading confirmed by a person",
  corrected: "entered from the paper by a person",
};

/** "2026-10-12" → "12 Oct 2026". Anything else is returned as given. */
export function showDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${Number(m[3])} ${month} ${m[1]}` : iso;
}

export function dateLine(r: Pick<TimelineResult, "date" | "date_source" | "date_machine_read">): string {
  const unchecked = r.date_machine_read ? ", read by the machine, not checked" : "";
  return `${showDate(r.date)} (${DATE_SOURCE_WORDS[r.date_source]}${unchecked})`;
}

/** "this visit" or "other visit" (never "earlier": the other visit may be the later one). */
export function visitWords(current: boolean): string {
  return current ? "this visit" : "other visit";
}

export function visitsLine(count: number): string {
  return count <= 1 ? "Only this visit is included." : `This visit and ${count - 1} linked visit${count - 1 === 1 ? "" : "s"} are included.`;
}

/** One result in words: the value, how it was checked, its range flags and how it compares with the previous one. */
export function resultLine(r: TimelineResult): string {
  const parts = [r.shown, OUTCOME_WORDS[r.outcome], ...r.flags.map((f) => f.text)];
  if (r.change) parts.push(r.change.text);
  return parts.join(" · ");
}
