// The intake journey and who can do each step. Mirrors backend permissions (docs/06, docs/11): a patient account
// can start a case, give consent, record voice and mark the body map; documents, AI follow-ups and the triage form
// need the health-worker (ANM) account that created the case. Pure, unit-tested.

export const STEPS = [
  { id: "case", path: "/intake", label: "Case", title: "Facility and situation" },
  { id: "consent", path: "/intake/consent", label: "Consent", title: "Consent" },
  { id: "voice", path: "/intake/voice", label: "Voice", title: "Describe symptoms" },
  { id: "body", path: "/intake/body-map", label: "Body map", title: "Where it hurts" },
  { id: "documents", path: "/intake/documents", label: "Reports", title: "Lab reports" },
  { id: "followup", path: "/intake/follow-up", label: "Questions", title: "Follow-up questions" },
  { id: "review", path: "/intake/review", label: "Review", title: "Review and triage" },
] as const;
export type StepId = (typeof STEPS)[number]["id"];

export type ConsentState = "not_provided" | "granted" | "declined" | "withdrawn";
export type Availability = "open" | "needs_consent" | "health_worker_only";

const WORKER_ONLY: StepId[] = ["documents", "followup", "review"];

export function availability(step: StepId, role: string | null, triageConsent: ConsentState | null): Availability {
  if (step === "case" || step === "consent") return "open";
  if (WORKER_ONLY.includes(step) && role !== "anm") return "health_worker_only";
  return triageConsent === "granted" ? "open" : "needs_consent";
}

export function stepIndex(id: StepId): number {
  return STEPS.findIndex((s) => s.id === id);
}

export function hrefFor(id: StepId, caseId: string | null): string {
  const s = STEPS[stepIndex(id)];
  return caseId && id !== "case" ? `${s.path}?case=${caseId}` : s.path;
}

// The next step this role can actually use (skips health-worker-only steps for a patient).
export function nextStep(id: StepId, role: string | null, triageConsent: ConsentState | null): StepId | null {
  for (let i = stepIndex(id) + 1; i < STEPS.length; i++) {
    const s = STEPS[i].id;
    if (availability(s, role, triageConsent) === "open") return s;
  }
  return null;
}
