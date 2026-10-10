"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Icon, type IconName } from "@/components/Icon";
import { IntakeShell } from "@/components/IntakeShell";
import { Button, Field, Notice, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { hrefFor } from "@/lib/steps";
import { SCENARIOS, type Scenario } from "@/lib/triageForm";

// The seven scenarios are the backend's fixed list (app/rules/models.Scenario). Descriptions say who the flow is
// for; they are not clinical claims.
const SCENARIO_INFO: Record<Scenario, { title: string; text: string; icon: IconName }> = {
  opd: { title: "Outpatient visit", text: "Someone who has come in feeling unwell.", icon: "building" },
  maternal: { title: "Pregnancy care", text: "A pregnant woman: checks for pregnancy danger signs.", icon: "people" },
  chronic_ncd: { title: "Long-term condition", text: "Follow-up for blood pressure, diabetes and similar.", icon: "clipboard" },
  health_camp: { title: "Health camp", text: "Screening at a community health camp.", icon: "people" },
  campus_fever: { title: "Campus fever", text: "Fever at a school, college or hostel.", icon: "building" },
  occupational: { title: "Work-related", text: "Health checks linked to work, such as noise exposure.", icon: "shield" },
  referral: { title: "Referral", text: "A patient being referred to another facility.", icon: "arrowRight" },
};

const FACILITY_PATTERN = /^[A-Z0-9-]{3,32}$/;
const CASE_CODE_PATTERN = /^PT-[0-9A-F]{12}$/; // same pattern as the backend's HandoverRequest

export default function IntakeHome() {
  const router = useRouter();
  const [role, setRole] = useState<string | null>(null);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [facility, setFacility] = useState("");
  const [errors, setErrors] = useState<{ facility?: string; scenario?: string }>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [claiming, setClaiming] = useState(false);

  useEffect(() => {
    api
      .get<{ role: string }>("auth/me")
      .then((me) => setRole(me.role))
      .catch(() => setServerError("Could not check your session. Please sign in again."));
  }, []);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    const code = facility.trim().toUpperCase();
    const errs: typeof errors = {};
    if (!FACILITY_PATTERN.test(code)) errs.facility = "Enter a facility code of 3–32 capital letters, numbers or dashes, for example PHC-KHURDA-01";
    if (!scenario) errs.scenario = "Choose the situation that fits this visit";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setServerError(null);
    try {
      const created = await api.post<{ case_id: string }>("cases", { scenario, facility_code: code });
      router.push(`/intake/consent?case=${created.case_id}`);
    } catch (err) {
      setServerError(err instanceof ApiError ? `Could not start the case: ${err.message}${err.requestId ? ` (reference ${err.requestId})` : ""}` : "Could not reach the server. Nothing was created; try again.");
      setBusy(false);
    }
  }

  // An ANM takes over a case the patient started on their own account (POST /cases/handover).
  async function takeOver(e: React.FormEvent) {
    e.preventDefault();
    const token = code.trim().toUpperCase();
    if (!CASE_CODE_PATTERN.test(token)) {
      setCodeError("Enter the case code exactly as the patient's screen shows it, for example PT-1A2B3C4D5E6F");
      return;
    }
    setClaiming(true);
    setCodeError(null);
    try {
      const claimed = await api.post<{ case_id: string }>("cases/handover", { patient_token: token });
      router.push(hrefFor("documents", claimed.case_id));
    } catch (err) {
      setCodeError(
        err instanceof ApiError && err.status === 404
          ? "No patient case with this code was found in your facility. Check the code with the patient."
          : err instanceof ApiError && err.code === "CASE_ALREADY_HANDED_OVER"
            ? "Another health worker has already taken over this case."
            : err instanceof ApiError && err.status === 403
              ? "The patient has not agreed to triage, or withdrew. Their consent is needed before you can continue."
              : "Could not continue the case. Nothing was changed; try again.",
      );
      setClaiming(false);
    }
  }

  return (
    <IntakeShell step="case" caseId={null} role={role} triage={null} intro="Choose where this visit happens and what kind of visit it is. No name or ID number is collected: the case gets a random token.">
      <form onSubmit={start} noValidate className="space-y-6 rounded-[16px] border border-white/60 bg-card p-4 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)] sm:p-6">
        {serverError && (
          <Notice tone="error" role="alert">
            {serverError}
          </Notice>
        )}

        <Field
          id="facility"
          label="Facility code"
          hint="Type the code of your health facility. This prototype has no facility directory, so the code is not checked against a list."
          error={errors.facility}
        >
          <input
            id="facility"
            className={`${inputClass} max-w-sm font-mono uppercase`}
            value={facility}
            onChange={(e) => setFacility(e.target.value.toUpperCase())}
            placeholder="PHC-KHURDA-01"
            autoComplete="off"
            aria-describedby={["facility-hint", errors.facility ? "facility-error" : ""].filter(Boolean).join(" ")}
          />
        </Field>

        <fieldset aria-describedby={errors.scenario ? "scenario-error" : undefined} className={errors.scenario ? "border-l-4 border-error pl-3" : ""}>
          <legend className="text-lg font-bold">What kind of visit is this?</legend>
          {errors.scenario && (
            <p id="scenario-error" className="mt-1 font-bold text-error">
              <span className="sr-only">Error: </span>
              {errors.scenario}
            </p>
          )}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {SCENARIOS.map((s) => {
              const info = SCENARIO_INFO[s];
              const selected = scenario === s;
              return (
                <label
                  key={s}
                  className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-[12px] border bg-surface-2 p-4 transition-all duration-150 motion-reduce:transition-none ${selected ? "border-primary/50 shadow-[inset_1px_1px_3px_rgba(0,0,0,0.05),inset_-1px_-1px_3px_rgba(255,255,255,0.5)]" : "border-white/60 shadow-[4px_4px_10px_rgba(0,0,0,0.03),-4px_-4px_10px_rgba(255,255,255,0.8)] hover:shadow-[inset_1px_1px_2px_rgba(0,0,0,0.02)] hover:-translate-y-px"}`}
                >
                  <input type="radio" name="scenario" value={s} checked={selected} onChange={() => setScenario(s)} className="mt-1 size-5 accent-[var(--primary)]" />
                  <span className="flex-1">
                    <span className="flex items-center gap-2 font-bold text-ink">
                      <Icon name={info.icon} className="text-primary" /> {info.title}
                    </span>
                    <span className="block text-muted">{info.text}</span>
                  </span>
                  {selected && <Icon name="check" className="mt-1 text-primary" label="Selected" />}
                </label>
              );
            })}
          </div>
          <p className="mt-2 text-sm text-muted">Every situation uses the same core checks. Extra questions specific to some situations are not in this prototype yet.</p>
        </fieldset>

        {role === "anm" && (
          <Notice tone="info" title="Starting as a health worker">
            You will take the patient&apos;s consent next. Read the notice to them in their language.
          </Notice>
        )}

        <Button type="submit" disabled={busy}>
          {busy ? "Starting…" : "Start case"} <Icon name="arrowRight" />
        </Button>
      </form>

      {role === "anm" && (
        <form onSubmit={takeOver} noValidate className="space-y-4 rounded-[16px] border border-white/60 bg-card p-4 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)] sm:p-6">
          <h2 className="text-lg font-bold">Continue a patient&apos;s case</h2>
          <p className="text-muted">
            If the patient started on their own account, enter the case code from their screen. You then do the reports, follow-up questions and
            triage form with them. Their consent, voice recording and body map are already saved.
          </p>
          <Field id="case-code" label="Case code" error={codeError ?? undefined}>
            <input
              id="case-code"
              className={`${inputClass} max-w-sm font-mono uppercase`}
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="PT-1A2B3C4D5E6F"
              autoComplete="off"
              aria-describedby={codeError ? "case-code-error" : undefined}
            />
          </Field>
          <Button type="submit" variant="secondary" disabled={claiming}>
            {claiming ? "Opening…" : "Continue case"} <Icon name="arrowRight" />
          </Button>
        </form>
      )}
    </IntakeShell>
  );
}
