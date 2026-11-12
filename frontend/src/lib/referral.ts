// Referral closure tracking (docs/21): types and pure helpers for the Refer panel and the tracking page. No imports,
// so `node --test` runs it directly. The server decides everything (who may refer, which step comes next, overdue);
// these helpers only word the server's answers and check the form before it is sent.

export type Urgency = "RED" | "YELLOW" | "GREEN";
export type ReferralStatus = "referred" | "in_transit" | "reached" | "seen" | "outcome_recorded" | "not_reached";
export type Option = { code: string; label: string };
export type FacilityView = { code: string; name: string; level: string | null; district: string | null };
export type HistoryRow = { status: ReferralStatus; label: string; detail_code?: string | null; detail_label?: string | null; at: string; actor_role: string };

export type Referral = {
  referral_id: string;
  case_id: string;
  patient_token: string;
  from_facility: FacilityView;
  to_facility: FacilityView;
  urgency: Urgency;
  triage_run_id: string;
  status: ReferralStatus;
  status_label: string;
  created_at: string;
  last_update_at: string;
  hours_since_update: number;
  overdue: boolean;
  overdue_threshold_hours: number;
  status_history?: HistoryRow[];
  allowed_next?: ReferralStatus[];
  reason?: Option;
  transport?: { mode: string; label: string; escort_required: boolean; travel_minutes: number | null };
  insurance?: { scheme: string; label: string };
};

export type ReferralOptions = {
  destinations: FacilityView[];
  reasons: Option[];
  transport: Option[];
  insurance: Option[];
  outcomes: Option[];
  barriers: Option[];
  statuses: Option[];
  facility_list_note: string;
};

export type CaseReferrals = {
  case_id: string;
  clinical_content_available: boolean;
  can_refer: boolean;
  refer_blocked: string | null;
  latest_triage_run_id: string | null;
  overdue_threshold_hours: number;
  referrals: Referral[];
  options?: ReferralOptions;
};

export type Tracking = {
  generated_at: string;
  overdue_threshold_hours: number;
  counts: { referred: number; in_transit: number; reached: number; seen: number; overdue: number };
  items: Referral[];
};

export type LetterVital = { field: string; label: string; value: string | number; unit: string | null };
export type ReferralLetter = {
  title: string;
  referral_id: string;
  case_id: string;
  patient_token: string;
  created_at: string;
  from_facility: FacilityView;
  to_facility: FacilityView;
  urgency: { level: Urgency; meaning: string; rules_urgency: Urgency; source: string; reviewer_recorded: Urgency | null };
  reason: Option;
  scenario: Option;
  patient: { age_years: number | null; pregnant: boolean; trimester: number | null };
  determination: string | null;
  missing_fields: string[];
  flags: { rule_id: string; urgency: Urgency; reason: string; source: string; evidence: { field: string; value: unknown; threshold: string }[] }[];
  vitals: LetterVital[];
  transport: { mode: string; label: string; escort_required: boolean; travel_minutes: number | null };
  insurance: { scheme: string; label: string };
  sign_off: { reviewer_role: string; signed_off_at: string; statement: string };
  triage: { run_id: string; evaluated_at: string | null; recorded_at: string; engine_version: string | null; ruleset_version: string | null };
  status: ReferralStatus;
  status_history: HistoryRow[];
  disclaimer: string;
};

/** Why the medical officer cannot refer now (server `refer_blocked` codes). */
export const BLOCKED_WORDS: Record<string, string> = {
  no_triage_run: "No triage run is recorded for this case yet.",
  sign_off_required: "Sign off the latest triage run first. A referral is made from a signed-off triage.",
  referral_open: "This case already has an open referral. Record how it ended before referring again.",
  consent: "Triage consent is not in effect, so this case cannot be referred.",
  role: "Only a medical officer can refer a patient.",
};

/** Button words for the next step a person records. */
export const NEXT_STEP_WORDS: Record<ReferralStatus, string> = {
  referred: "Referred",
  in_transit: "Mark on the way",
  reached: "Mark reached",
  seen: "Mark seen",
  outcome_recorded: "Record outcome",
  not_reached: "Did not reach",
};

/** A closing step needs a code: an outcome after "seen", a barrier when the patient did not reach. */
export function detailKind(status: ReferralStatus): "outcome" | "barrier" | null {
  return status === "outcome_recorded" ? "outcome" : status === "not_reached" ? "barrier" : null;
}

export type ReferralForm = {
  to_facility: string;
  reason_code: string;
  transport_mode: string;
  escort: "" | "yes" | "no";
  travel_minutes: string;
  insurance_scheme: string;
};

export const EMPTY_FORM: ReferralForm = { to_facility: "", reason_code: "", transport_mode: "", escort: "", travel_minutes: "", insurance_scheme: "" };

export function formErrors(f: ReferralForm): Partial<Record<keyof ReferralForm, string>> {
  const e: Partial<Record<keyof ReferralForm, string>> = {};
  if (!f.to_facility) e.to_facility = "Choose the facility the patient is referred to.";
  if (!f.reason_code) e.reason_code = "Choose the reason for referral.";
  if (!f.transport_mode) e.transport_mode = "Choose how the patient will travel.";
  if (!f.escort) e.escort = "Say whether someone must go with the patient.";
  if (!f.insurance_scheme) e.insurance_scheme = "Choose the insurance scheme, or Not known.";
  const minutes = f.travel_minutes.trim();
  if (minutes && !(/^\d+$/.test(minutes) && Number(minutes) >= 1 && Number(minutes) <= 1440)) {
    e.travel_minutes = "Travel time is whole minutes from 1 to 1440. Leave it empty if not known.";
  }
  return e;
}

export function toRequest(f: ReferralForm, triageRunId: string) {
  const minutes = f.travel_minutes.trim();
  return {
    triage_run_id: triageRunId,
    to_facility: f.to_facility,
    reason_code: f.reason_code,
    transport_mode: f.transport_mode,
    escort_required: f.escort === "yes",
    travel_minutes: minutes ? Number(minutes) : null,
    insurance_scheme: f.insurance_scheme,
    confirm: true as const,
  };
}

export function sinceText(hours: number, overdue: boolean, thresholdHours: number): string {
  if (overdue) return `Overdue: no update for ${hours} h (limit ${thresholdHours} h)`;
  return hours < 1 ? "Updated within the last hour" : `${hours} h since the last update`;
}

export function vitalLine(v: LetterVital): string {
  return `${v.label}: ${v.value}${v.unit ? ` ${v.unit}` : ""}`;
}

/** A safe download name: the patient token and the start of the referral id, nothing else. */
export function fhirFileName(r: { patient_token: string; referral_id: string }): string {
  return `referral-${r.patient_token}-${r.referral_id.slice(0, 8)}.fhir.json`.replace(/[^A-Za-z0-9._-]/g, "-");
}
