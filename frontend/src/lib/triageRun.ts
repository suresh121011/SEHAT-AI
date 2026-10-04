// Optimistic-concurrency helpers for intake triage submission (POST /cases/{id}/triage?expected_run_id=…).
// Pure: no React, no DOM. The server decides staleness; the client only says which run it last saw and never
// resubmits on its own.

/** The run the client believes is latest: its own last successful submit wins over an older case read. */
export function expectedRunId(caseLatestRunId: string | null | undefined, lastSubmittedRunId: string | null | undefined): string {
  return lastSubmittedRunId || caseLatestRunId || "none";
}

export function triagePath(caseId: string, expected: string): string {
  return `cases/${encodeURIComponent(caseId)}/triage?expected_run_id=${encodeURIComponent(expected)}`;
}

export const STALE_RUN_CODES = ["STALE_TRIAGE_RUN", "EXPECTED_RUN_REQUIRED"] as const;

export const STALE_RUN_MESSAGE = "Stop: a newer triage result exists for this patient. Nothing was saved. Reload and review the latest result before submitting again.";

/** True for the two 409s that mean "someone recorded a newer run" (or the client sent no expectation). */
export function isStaleRunConflict(status: number, code: string): boolean {
  return status === 409 && (STALE_RUN_CODES as readonly string[]).includes(code);
}
