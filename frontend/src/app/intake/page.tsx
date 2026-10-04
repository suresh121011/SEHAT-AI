"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Icon, type IconName } from "@/components/Icon";
import { IntakeShell } from "@/components/IntakeShell";
import { Button, Field, Notice, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
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

export default function IntakeHome() {
  const router = useRouter();
  const [role, setRole] = useState<string | null>(null);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [facility, setFacility] = useState("");
  const [errors, setErrors] = useState<{ facility?: string; scenario?: string }>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  return (
    <IntakeShell step="case" caseId={null} role={role} triage={null} intro="Choose where this visit happens and what kind of visit it is. No name or ID number is collected: the case gets a random token.">
      <form onSubmit={start} noValidate className="space-y-6">
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
          <legend className="text-xl font-bold">What kind of visit is this?</legend>
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
                  className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border-2 bg-card p-4 transition-colors motion-reduce:transition-none ${selected ? "border-primary bg-primary-tint" : "border-subtle hover:border-line"}`}
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
    </IntakeShell>
  );
}
