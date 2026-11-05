// SEHAT patient code view logic (backend app/patient_registry.py). Pure functions, tested with node --test. The server
// validates every code (format and check character); this only formats what is typed and words the outcomes.
// Errors are read by their `code` field (an ApiError from lib/api), so this module needs no runtime import.

export type LinkedVisit = { case_id: string; visit_date: string; facility_code: string; scenario: string };
export type PatientLink = {
  case_id: string;
  linked: boolean;
  link_visits_consent: string;
  patient_code: string | null;
  basis?: "new_code" | "patient_code" | null;
  linked_by_role?: string;
  linked_at?: string;
  other_visits: LinkedVisit[];
  other_visits_hidden_reason?: string | null;
};
export type Lookup = { patient_code: string; visible_visits: number; first_visit_date: string | null; already_linked_here: boolean };

/** The 8 code characters (the box shows "SP-" outside it). The prefix is dropped only from a whole pasted code
 * ("SP-XXXX-XXXX", 10 characters), exactly as the server does: a code may itself start with S or P. */
function codeChars(text: string): string {
  const t = text.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return (t.length >= 10 && t.startsWith("SP") ? t.slice(2) : t).slice(0, 8);
}

/** What the health worker typed, upper-cased and grouped like the slip ("XXXX-XXXX"); never validated here. */
export function formatTyped(text: string): string {
  const t = codeChars(text);
  return t.length > 4 ? `${t.slice(0, 4)}-${t.slice(4)}` : t;
}

/** Ready to send: 8 code characters (the server still checks the check character). */
export function looksComplete(text: string): boolean {
  return codeChars(text).length === 8;
}

export function explainPatientError(err: unknown): string {
  const e = (typeof err === "object" && err !== null ? err : {}) as { status?: unknown; code?: unknown; message?: unknown };
  const code = "status" in e && typeof e.code === "string" ? e.code : null;
  if (!code) return "Could not reach the server. Nothing was recorded; try again.";
  switch (code) {
    case "PATIENT_CODE_INVALID":
      return "This code is not valid — one character is probably mistyped. Check each character on the slip and type it again.";
    case "NOT_FOUND":
      return "No patient with this code in your facilities. Check the slip; if it is right, give the patient a new code instead.";
    case "VISIT_ALREADY_LINKED":
      return "This visit is already linked to a patient. If that link is wrong, unlink it first.";
    case "VISIT_NOT_LINKED":
      return "This visit is not linked to a patient.";
    case "CONSENT_REQUIRED":
      return "The patient has not agreed to linking visits for this visit. Record that choice on the consent step first.";
    default:
      return typeof e.message === "string" ? e.message : "Something went wrong. Nothing was recorded.";
  }
}

export const BASIS_WORDS: Record<string, string> = { new_code: "new code given at this visit", patient_code: "code shown by the patient" };
