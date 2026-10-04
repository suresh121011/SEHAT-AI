"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { activateCase } from "@/lib/intakeStore";
import type { ConsentState } from "@/lib/steps";

export type CaseView = {
  case_id: string;
  patient_token: string;
  scenario: string;
  facility_code: string;
  status: string;
  is_creator: boolean;
  /** Latest triage run on this case (null before the first run). Sent back as `expected_run_id` on re-triage.
   * Optional because an older server may not send it; absent is treated as "no run known". */
  latest_triage_run_id?: string | null;
  consent: { triage: ConsentState; ai_assist: ConsentState; voice_cloud: ConsentState };
};

// Shared loading of the case (?case=) and the signed-in role, used by every intake step.
export function useCase() {
  const caseId = useSearchParams().get("case");
  const [role, setRole] = useState<string | null>(null);
  const [caseView, setCaseView] = useState<CaseView | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!caseId) return null;
    try {
      const cv = await api.get<CaseView>(`cases/${caseId}`);
      setCaseView(cv);
      setLoadError(null);
      return cv;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 400)) setNotFound(true);
      else setLoadError("Could not load this case. Check the connection and try again.");
      return null;
    }
  }, [caseId]);

  useEffect(() => {
    if (caseId) activateCase(caseId); // a different case clears the previous case's local notes
    api
      .get<{ role: string }>("auth/me")
      .then((me) => setRole(me.role))
      .catch(() => setLoadError("Could not check your session. Please sign in again."));
    reload();
  }, [caseId, reload]);

  return { caseId, role, caseView, notFound: notFound || !caseId, loadError, reload };
}

export function explainError(err: unknown, fallback = "Something went wrong. Nothing was saved; you can try again."): string {
  if (!(err instanceof ApiError)) return fallback;
  return `${err.message}${err.requestId ? ` (reference ${err.requestId})` : ""}`;
}
