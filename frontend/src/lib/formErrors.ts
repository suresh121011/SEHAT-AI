// Form error helpers for the reviewer dialogs (no React, no DOM; unit-tested with `node --test`).
// The server stays authoritative: these helpers only (1) put the server's own VALIDATION_ERROR details next to the
// control they belong to, and (2) say which kind of failure happened so the dialog can word it honestly. Nothing
// here decides whether a value is clinically acceptable.

/** One entry of a 400 VALIDATION_ERROR's `details.errors` (field = body path without "body"; "" = whole request). */
export type ServerFieldError = { field: string; constraint?: string };

export type SummaryItem = {
  /** Control key the message belongs to (the summary links to it), or null when it maps to no control. */
  key: string | null;
  message: string;
};

export type MappedErrors = {
  /** Control key → message shown next to that control. */
  fields: Record<string, string>;
  /** Messages that belong to no control (model-level errors, unknown paths). Shown in the summary only. */
  unmapped: string[];
};

/** Read `details.errors` defensively: anything malformed is dropped rather than guessed at. */
export function serverFieldErrors(details: Record<string, unknown> | null | undefined): ServerFieldError[] {
  const raw = details?.errors;
  if (!Array.isArray(raw)) return [];
  const out: ServerFieldError[] = [];
  for (const e of raw) {
    if (!e || typeof e !== "object") continue;
    const o = e as Record<string, unknown>;
    if (typeof o.field !== "string") continue;
    out.push({ field: o.field, constraint: typeof o.constraint === "string" ? o.constraint : undefined });
  }
  return out;
}

/**
 * Map server field errors to dialog controls.
 * `resolve(field)` returns the control key for a server path, or null when no control shows that value.
 * The first message per control wins (one message next to each control); every message is kept somewhere.
 */
export function mapServerErrors(errors: ServerFieldError[], resolve: (field: string) => string | null): MappedErrors {
  const fields: Record<string, string> = {};
  const unmapped: string[] = [];
  for (const e of errors) {
    const constraint = e.constraint?.trim() || "Invalid value";
    const key = e.field ? resolve(e.field) : null;
    if (key === null) {
      unmapped.push(e.field ? `${e.field.replaceAll("_", " ")}: ${constraint}` : `The request as a whole: ${constraint}`);
      continue;
    }
    if (!(key in fields)) fields[key] = constraint;
    else if (!fields[key].includes(constraint)) fields[key] = `${fields[key]}; ${constraint}`;
  }
  return { fields, unmapped };
}

/** Exact-path resolver from a table (server path → control key). Unknown paths → null (summary only). */
export function tableResolver(table: Record<string, string>): (field: string) => string | null {
  return (field) => (Object.prototype.hasOwnProperty.call(table, field) ? table[field] : null);
}

/**
 * Summary list in on-screen control order: client and server messages for known controls first (in `order`), then
 * messages that belong to no control. Client messages are shown before server ones for the same control.
 */
export function buildSummary(order: string[], client: Record<string, string>, server: MappedErrors | null, label: (key: string) => string): SummaryItem[] {
  const items: SummaryItem[] = [];
  const seen = new Set<string>();
  const add = (key: string) => {
    if (seen.has(key)) return;
    const msg = client[key] ?? server?.fields[key];
    if (!msg) return;
    seen.add(key);
    items.push({ key, message: `${label(key)}: ${msg}` });
  };
  for (const k of order) add(k);
  for (const k of [...Object.keys(client), ...Object.keys(server?.fields ?? {})]) add(k);
  for (const m of server?.unmapped ?? []) items.push({ key: null, message: m });
  return items;
}

/** Merge client and server messages per control (client first). Used for the text next to each control. */
export function fieldMessage(key: string, client: Record<string, string>, server: MappedErrors | null): string | undefined {
  return client[key] ?? server?.fields[key];
}

// ── Failure kind (drives the wording; the server's code is shown, never reinterpreted) ──

export type FailureKind = "validation" | "authorization" | "session" | "conflict" | "pii" | "not_found" | "server" | "network";

/** Classify a failure from its HTTP status (status 0 / undefined = no response reached us). */
export function failureKind(status: number | null | undefined, code?: string | null): FailureKind {
  if (!status) return "network";
  if (code === "PII_DETECTED") return "pii";
  if (status === 400 || (status === 422 && code === "VALIDATION_ERROR")) return "validation";
  if (status === 401) return "session";
  if (status === 403) return "authorization";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "pii";
  return "server";
}

/** A heading for the error notice, by failure kind. */
export const FAILURE_TITLE: Record<FailureKind, string> = {
  validation: "The server did not accept some values",
  authorization: "Not allowed",
  session: "Session ended",
  conflict: "The case changed or the request conflicts with what is recorded",
  pii: "Possible identifier in the text",
  not_found: "Not found",
  server: "Server error",
  network: "No answer from the server",
};
