"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { SourceTag, roleWords, type SourceKind } from "@/components/Provenance";
import { Button, ButtonLink, Card, Field, Notice, Spinner, inputClass } from "@/components/ui";
import { type Counterfactuals, type TriageResult, UrgencyResult } from "@/components/UrgencyResult";
import { ApiError, api } from "@/lib/api";
import { labelOf } from "@/lib/bodyMap";
import { clearNotes, readNotes, type LocalNotes } from "@/lib/intakeStore";
import { hrefFor } from "@/lib/steps";
import {
  ATP_FLAGS,
  CONSCIOUSNESS,
  type AiReviewed,
  type FieldKey,
  type FormState,
  type Prefill,
  type Scenario,
  SCENARIOS,
  type VoicePrefill,
  buildTriageInput,
  emptyForm,
  fieldForErrorPath,
  mergePrefill,
} from "@/lib/triageForm";
import { STALE_RUN_MESSAGE, expectedRunId, isStaleRunConflict, triagePath } from "@/lib/triageRun";
import { useCase } from "@/lib/useCase";

type DocReviewed = { values: { field_id: string; name: string; outcome: string; value: Record<string, unknown>; source: { type: string; resolved_by_role: string | null } }[]; unresolved?: { document_id: string; field_id?: string; state: string }[] };
type Phase = "edit" | "check" | "submitting" | "done";

const VITALS: { key: FieldKey; label: string; hint: string; inputMode: "numeric" | "decimal" }[] = [
  { key: "spo2", label: "Oxygen saturation (SpO2, %)", hint: "Whole number, 0–100", inputMode: "numeric" },
  { key: "pulse", label: "Pulse (beats per minute)", hint: "Whole number", inputMode: "numeric" },
  { key: "resp_rate", label: "Breathing rate (per minute)", hint: "Count for a full minute", inputMode: "numeric" },
  { key: "sbp", label: "Blood pressure: systolic (top number)", hint: "mmHg", inputMode: "numeric" },
  { key: "dbp", label: "Blood pressure: diastolic (bottom number)", hint: "mmHg", inputMode: "numeric" },
  { key: "temp_c", label: "Temperature (°C)", hint: "For example 38.5. Convert °F first.", inputMode: "decimal" },
];

// Follow-up question field names (backend required_fields) → the form field they inform.
const FOLLOWUP_TO_FIELD: Record<string, FieldKey> = { spo2: "spo2", pulse: "pulse", resp_rate: "resp_rate", temp: "temp_c", bp: "sbp", bp_reading_1: "sbp", red_flag_screen: "red_flag_screen_completed", danger_signs: "red_flag_screen_completed" };

function sourceKind(kind: string): SourceKind {
  return kind as SourceKind;
}

function ReviewScreen() {
  const { caseId, role, caseView, notFound, loadError, reload } = useCase();
  const [form, setForm] = useState<FormState | null>(null);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [touched, setTouched] = useState<Set<FieldKey>>(new Set());
  const [docs, setDocs] = useState<DocReviewed | null>(null);
  const [sourceNotes, setSourceNotes] = useState<string[]>([]);
  const [notes, setNotes] = useState<LocalNotes>({ bodyMap: [], followUps: {} });
  const [bodyMap, setBodyMap] = useState<{ regions: string[]; recorded_by_role: string | null } | null>(null);
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("edit");
  const [result, setResult] = useState<{ run_id: string; result: TriageResult } | null>(null);
  const [cf, setCf] = useState<Counterfactuals | "loading" | "unavailable">("loading");
  const [loading, setLoading] = useState(true);
  const [aiPending, setAiPending] = useState<{ field: string; state: string }[]>([]);
  const [heldBack, setHeldBack] = useState<string[]>([]);
  const summaryRef = useRef<HTMLDivElement>(null);
  const loaded = useRef(false); // checked values load once; a later case reload never resets the form or the screen
  // The run this screen itself last recorded; it wins over an older case read for any later resubmit.
  const lastRunId = useRef<string | null>(null);

  const isWorker = role === "anm";
  const triage = caseView?.consent.triage ?? null;

  const load = useCallback(async () => {
    if (!caseId || !caseView || !isWorker || caseView.consent.triage !== "granted") {
      setLoading(false);
      return;
    }
    if (loaded.current) return;
    loaded.current = true;
    setLoading(true);
    const why: string[] = [];
    const voice = await api.get<VoicePrefill>(`cases/${caseId}/voice/prefill`).catch((e) => {
      why.push(e instanceof ApiError && e.code === "FEATURE_DISABLED" ? "Voice input is off on this server." : "Voice values could not be loaded.");
      return null;
    });
    let ai: AiReviewed | null = null;
    if (caseView.consent.ai_assist === "granted") {
      ai = await api.get<AiReviewed>(`cases/${caseId}/ai/reviewed`).catch(() => {
        why.push("AI-reviewed values could not be loaded.");
        return null;
      });
    } else why.push("AI assistance was not agreed, so no AI-extracted values are used.");
    const d = await api.get<DocReviewed>(`cases/${caseId}/documents/reviewed`).catch(() => null);
    const bm = await api.get<{ regions: string[]; recorded_by_role: string | null }>(`cases/${caseId}/body-map`).catch(() => {
      why.push("The saved body map could not be loaded.");
      return null;
    });
    const p = mergePrefill(voice, ai);
    const scenario = (SCENARIOS as readonly string[]).includes(caseView.scenario) ? (caseView.scenario as Scenario) : "opd";
    setForm((f) => f ?? { ...emptyForm(scenario), ...p.values });
    setPrefill(p);
    setDocs(d);
    setSourceNotes(why);
    setAiPending((ai?.unresolved ?? []).map((u) => ({ field: u.field, state: u.state })));
    setHeldBack(ai?.conflicting_readings ?? []);
    setNotes(readNotes(caseId));
    setBodyMap(bm);
    setLoading(false);
  }, [caseId, caseView, isWorker]);

  useEffect(() => {
    load();
  }, [load]);

  const scenario = form?.scenario ?? "opd";
  const answersFor = useMemo(() => {
    const m: Partial<Record<FieldKey, { question: string; answer: string }[]>> = {};
    for (const [name, a] of Object.entries(notes.followUps)) {
      const k = FOLLOWUP_TO_FIELD[name];
      if (k) (m[k] ??= []).push(a);
    }
    return m;
  }, [notes]);
  const otherAnswers = Object.entries(notes.followUps).filter(([name]) => !FOLLOWUP_TO_FIELD[name]);

  if (notFound) return <CaseNotFound />;

  function set<K extends FieldKey>(k: K, v: FormState[K]) {
    // Unticking "pregnant" also clears the trimester, so no hidden value can block the form.
    setForm((f) => (f ? { ...f, [k]: v, ...(k === "pregnant" && v === false ? { trimester: "" as const } : {}) } : f));
    setTouched((t) => new Set(t).add(k));
    setErrors((e) => ({ ...e, [k]: undefined }));
  }

  function provenance(k: FieldKey) {
    if (!prefill) return null;
    const srcs = prefill.sources[k];
    const edited = touched.has(k);
    if (edited) return <SourceTag kind="typed" />;
    if (prefill.conflicts.includes(k)) return <Notice tone="warning">Different values were checked for this (voice and AI). Left blank: ask the patient and enter it.</Notice>;
    if (!srcs || srcs.length === 0) return null;
    return (
      <span className="flex flex-wrap gap-1">
        {srcs.map((s, i) => (
          <SourceTag key={i} kind={sourceKind(s.kind)} detail={s.detail} />
        ))}
      </span>
    );
  }

  function answerHints(k: FieldKey) {
    const a = answersFor[k];
    if (!a) return null;
    return (
      <ul className="space-y-1">
        {a.map((x, i) => (
          <li key={i} className="text-sm">
            <SourceTag kind="local_note" /> Follow-up answer: <strong>{x.answer}</strong> <span className="text-muted">({x.question})</span>
          </li>
        ))}
      </ul>
    );
  }

  function check() {
    if (!form) return;
    const built = buildTriageInput(form);
    if (!built.ok) {
      setErrors(built.errors);
      setFormError("Some values need fixing before triage. See the messages below.");
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    setErrors({});
    setFormError(null);
    setPhase("check");
    requestAnimationFrame(() => document.getElementById("check-heading")?.focus());
  }

  async function submit() {
    if (!form || !caseId) return;
    const built = buildTriageInput(form);
    if (!built.ok) return;
    setPhase("submitting");
    try {
      // Optimistic concurrency: tell the server which run we last saw ("none" before the first run).
      const expected = expectedRunId(caseView?.latest_triage_run_id, lastRunId.current);
      const r = await api.post<{ run_id: string; result: TriageResult }>(triagePath(caseId, expected), built.input);
      lastRunId.current = r.run_id;
      setResult(r);
      clearNotes(caseId); // local notes have served their purpose; never leave them on a shared device
      setPhase("done");
      setCf("loading");
      api.get<Counterfactuals>(`cases/${caseId}/triage/runs/${r.run_id}/counterfactuals`).then(setCf).catch(() => setCf("unavailable"));
      requestAnimationFrame(() => document.getElementById("result-heading")?.focus());
    } catch (err) {
      setPhase("edit");
      if (err instanceof ApiError && isStaleRunConflict(err.status, err.code)) {
        // Someone recorded a newer run. Nothing was saved; reload the case so the next submit expects the newer run.
        // Never resubmit automatically: the worker must look at the latest result first.
        lastRunId.current = null;
        setFormError(STALE_RUN_MESSAGE);
        void reload();
      } else if (err instanceof ApiError && err.code === "VALIDATION_ERROR") {
        const list = (err.details.errors as { field: string; constraint: string }[] | undefined) ?? [];
        const mapped: Partial<Record<FieldKey, string>> = {};
        for (const e of list) {
          const k = fieldForErrorPath(e.field);
          if (k) mapped[k] = e.constraint;
        }
        setErrors(mapped);
        setFormError(Object.keys(mapped).length ? "The server rejected some values. See the messages below." : "The server rejected this form. Check the values and try again.");
      } else if (err instanceof ApiError && err.code === "CONSENT_REQUIRED") setFormError("Triage consent is not in effect for this case. Nothing was recorded.");
      else if (err instanceof ApiError && err.code === "SCENARIO_MISMATCH") setFormError("The situation does not match this case. Nothing was recorded.");
      else setFormError(err instanceof ApiError ? `${err.message}${err.requestId ? ` (reference ${err.requestId})` : ""}` : "Could not reach the server. Nothing was recorded; you can try again.");
      requestAnimationFrame(() => summaryRef.current?.focus());
    }
  }

  const shell = (children: React.ReactNode) => (
    <IntakeShell step="review" caseId={caseId} role={role} triage={triage} token={caseView?.patient_token} scenario={caseView?.scenario} intro="Check every value with the patient, complete the red-flag screen, then submit.">
      {children}
    </IntakeShell>
  );

  if (loadError) return shell(<Notice tone="error" role="alert">{loadError}</Notice>);
  if (!caseView || role === null) return shell(<Spinner label="Loading case…" />);
  if (!isWorker) return shell(<Notice tone="info" title="This step is done by the health worker">The health worker who started this case fills in and submits the triage form with you.</Notice>);
  if (triage !== "granted")
    return shell(
      <Notice tone="warning" title="Consent needed first">
        Triage consent is not in effect. <a href={hrefFor("consent", caseId)}>Go to consent</a>.
      </Notice>,
    );
  if (loading || !form) return shell(<Spinner label="Loading checked values…" />);

  const filled = (k: FieldKey) => String(form[k] ?? "").trim() !== "";
  const rows: { k: FieldKey; label: string; value: string }[] = [
    { k: "age_years", label: "Age", value: form.age_years },
    { k: "pregnant", label: "Pregnant", value: form.pregnant ? `Yes${form.trimester ? `, trimester ${form.trimester}` : ""}` : "No" },
    ...VITALS.map((v) => ({ k: v.key, label: v.label, value: String(form[v.key] ?? "") })),
    { k: "on_supplemental_oxygen", label: "On oxygen", value: form.on_supplemental_oxygen === "yes" ? "Yes" : form.on_supplemental_oxygen === "no" ? "No" : "" },
    { k: "consciousness", label: "How alert", value: CONSCIOUSNESS.find(([c]) => c === form.consciousness)?.[1] ?? "" },
    { k: "suspected_infection", label: "Infection suspected", value: form.suspected_infection ? "Yes" : "No" },
    { k: "red_flag_screen_completed", label: "Red-flag screen", value: form.red_flag_screen_completed ? (form.red_flags_present.length ? form.red_flags_present.map((f) => ATP_FLAGS.find(([k]) => k === f)?.[1]).join("; ") : "Done: none present") : "" },
  ];
  function rowSource(k: FieldKey) {
    if (!filled(k) || !prefill) return null;
    if (touched.has(k) || !prefill.sources[k]?.length) return <SourceTag kind="typed" />;
    return <SourceTag kind={sourceKind(prefill.sources[k]![0].kind)} />;
  }
  function change(k: FieldKey) {
    setPhase("edit");
    requestAnimationFrame(() => (document.getElementById(`f-${k}`)?.querySelector("input,select") as HTMLElement | null ?? document.getElementById(`f-${k}`))?.focus());
  }
  const summaryList = (
    <dl className="mt-3 divide-y divide-subtle">
      {rows.map((r) => (
        <div key={r.k} className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[1fr_1.2fr_auto] sm:items-center">
          <dt className="font-bold">{r.label}</dt>
          <dd className={r.value ? "" : "text-muted"}>
            {r.value || "Not provided (counts as missing, never as normal)"} {rowSource(r.k)}
          </dd>
          {phase !== "done" && (
            <dd>
              <button type="button" className="min-h-11 text-primary underline underline-offset-4" onClick={() => change(r.k)}>
                Change<span className="sr-only"> {r.label}</span>
              </button>
            </dd>
          )}
        </div>
      ))}
    </dl>
  );

  if (phase === "done" && result)
    return shell(
      <div className="space-y-4">
        <Notice tone="success" role="status" title="Triage recorded">
          The result below was saved to this case. Notes kept on this device (body map, follow-up answers) were cleared.
        </Notice>
        <UrgencyResult result={result.result} counterfactuals={cf} />
        <Card>
          <h2 className="text-xl font-bold">Values the rules used</h2>
          {summaryList}
        </Card>
        <details className="rounded-xl border border-subtle bg-card shadow-card p-4">
          <summary className="min-h-11 cursor-pointer py-2 font-bold">Details for the clinician</summary>
          <dl className="mt-2 grid gap-1 text-sm sm:grid-cols-[auto_1fr]">
            <dt className="font-bold">Case</dt>
            <dd className="font-mono">{caseView.patient_token}</dd>
            <dt className="font-bold">Triage run</dt>
            <dd className="font-mono">{result.run_id}</dd>
            <dt className="font-bold">Rules</dt>
            <dd>
              engine {result.result.engine_version}, ruleset {result.result.ruleset_version}
            </dd>
            <dt className="font-bold">Time on this device</dt>
            <dd>{new Date().toLocaleString()}</dd>
          </dl>
        </details>
        <div className="no-print flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => window.print()}>
            <Icon name="print" /> Print summary
          </Button>
          <ButtonLink href="/intake" variant="quiet">
            Start a new case
          </ButtonLink>
        </div>
      </div>,
    );

  if (phase === "check" || phase === "submitting") {
    return shell(
      <Card>
        <h2 id="check-heading" tabIndex={-1} className="text-2xl font-bold outline-none">
          Final check before triage
        </h2>
        {summaryList}
        <p className="mt-4">By submitting you confirm these values were checked with the patient. The fixed rules then decide the urgency.</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <Button onClick={submit} disabled={phase === "submitting"}>
            {phase === "submitting" ? <Spinner label="Submitting…" /> : "Submit for triage"}
          </Button>
          <Button variant="secondary" onClick={() => setPhase("edit")} disabled={phase === "submitting"}>
            Go back and edit
          </Button>
        </div>
      </Card>,
    );
  }

  return shell(
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        check();
      }}
      className="space-y-5"
    >
      {formError && (
        <div ref={summaryRef} tabIndex={-1} role="alert" className="rounded-lg border-2 border-error bg-error-bg p-4 outline-none">
          <p className="font-bold text-error">There is a problem</p>
          <p>{formError}</p>
          <ul className="mt-1 list-disc pl-5">
            {Object.entries(errors)
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <li key={k}>
                  {k === "scenario" ? (
                    v
                  ) : (
                    <a href={`#f-${k}`} className="text-error underline">
                      {v}
                    </a>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}

      {(sourceNotes.length > 0 || (prefill && Object.keys(prefill.sources).length > 0)) && (
        <Notice tone="info" title="Values filled in for you">
          <p>Only values a health worker already checked (voice read-back, or AI fields you accepted) are filled in. You can change any of them. Changing a value here changes what the rules use.</p>
          {sourceNotes.length > 0 && <p className="text-sm">{sourceNotes.join(" ")}</p>}
        </Notice>
      )}

      {aiPending.length > 0 && (
        <Notice tone="warning" title={`${aiPending.length} AI-extracted value(s) still need a decision`}>
          <p>
            They are not filled in here. Decide them on the <a href={hrefFor("followup", caseId)}>Questions step</a>, or enter the values yourself:{" "}
            {aiPending.map((u) => u.field.replace(/^(symptom|medication|red_flag):/, "").replaceAll("_", " ")).join(", ")}.
          </p>
        </Notice>
      )}
      {(docs?.unresolved?.length ?? 0) > 0 && (
        <Notice tone="warning" title={`${docs!.unresolved!.length} lab report item(s) not checked yet`}>
          <p>
            Unchecked report values are not shown here. Check them on the <a href={hrefFor("documents", caseId)}>Reports step</a>. Lab values never change the urgency.
          </p>
        </Notice>
      )}
      {heldBack.length > 0 && <Notice tone="warning">Repeated readings differ for {heldBack.join(", ")}, so they are not filled in. Ask the patient and enter the value.</Notice>}

      {scenario !== "opd" && scenario !== "maternal" && (
        <Notice tone="info">Extra questions for this situation are not in this form yet. The rules use the core values and the red-flag screen below.</Notice>
      )}

      <Card>
        <h2 className="text-xl font-bold">Patient</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <Field id="f-age_years" label="Age (years)" error={errors.age_years}>
            <input id="f-age_years" inputMode="numeric" className={inputClass} value={form.age_years} onChange={(e) => set("age_years", e.target.value)} aria-describedby={errors.age_years ? "f-age_years-error" : undefined} />
            {provenance("age_years")}
          </Field>
          <fieldset id="f-pregnant" className="space-y-2">
            <legend className="font-bold">Pregnant</legend>
            <label className="flex min-h-11 items-center gap-3">
              <input type="checkbox" className="size-5" checked={form.pregnant} disabled={scenario === "maternal"} onChange={(e) => set("pregnant", e.target.checked)} />
              Patient is pregnant{scenario === "maternal" && " (pregnancy care case)"}
            </label>
            {form.pregnant && (
              <Field id="f-trimester" label="Trimester" error={errors.trimester}>
                <select id="f-trimester" className={inputClass} value={form.trimester} onChange={(e) => set("trimester", e.target.value as FormState["trimester"])}>
                  <option value="">Not known</option>
                  <option value="1">First (up to 12 weeks)</option>
                  <option value="2">Second (13–27 weeks)</option>
                  <option value="3">Third (28 weeks onward)</option>
                </select>
              </Field>
            )}
            {errors.pregnant && <p className="font-bold text-error">{errors.pregnant}</p>}
          </fieldset>
        </div>
      </Card>

      <Card>
        <h2 className="text-xl font-bold">Measurements</h2>
        <p className="text-muted">Leave a value blank if it was not measured. Blank values count as missing and need human review.</p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          {VITALS.map((v) => (
            <Field key={v.key} id={`f-${v.key}`} label={v.label} hint={v.hint} error={errors[v.key]}>
              <input
                id={`f-${v.key}`}
                inputMode={v.inputMode}
                className={inputClass}
                value={String(form[v.key] ?? "")}
                onChange={(e) => set(v.key, e.target.value as never)}
                aria-describedby={[`f-${v.key}-hint`, errors[v.key] ? `f-${v.key}-error` : ""].filter(Boolean).join(" ")}
              />
              {provenance(v.key)}
              {answerHints(v.key)}
            </Field>
          ))}
          <fieldset id="f-on_supplemental_oxygen">
            <legend className="font-bold">On supplemental oxygen?</legend>
            <div className="mt-1 flex gap-4">
              {(["no", "yes"] as const).map((o) => (
                <label key={o} className="flex min-h-11 items-center gap-2">
                  <input type="radio" name="oxygen" className="size-5" checked={form.on_supplemental_oxygen === o} onChange={() => set("on_supplemental_oxygen", o)} />
                  {o === "yes" ? "Yes" : "No"}
                </label>
              ))}
            </div>
          </fieldset>
          <Field id="f-consciousness" label="How alert is the patient?" hint="ACVPU scale" error={errors.consciousness}>
            <select id="f-consciousness" className={inputClass} value={form.consciousness} onChange={(e) => set("consciousness", e.target.value as FormState["consciousness"])}>
              <option value="">Not checked</option>
              {CONSCIOUSNESS.map(([c, l]) => (
                <option key={c} value={c}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <label id="f-suspected_infection" className="mt-4 flex min-h-11 items-start gap-3">
          <input type="checkbox" className="mt-1 size-5" checked={form.suspected_infection} onChange={(e) => set("suspected_infection", e.target.checked)} />
          <span>
            Infection is suspected
            <span className="block text-sm text-muted">Turns on the sepsis screening check (qSOFA).</span>
          </span>
        </label>
      </Card>

      <Card>
        <h2 className="text-xl font-bold">Red-flag screen</h2>
        <p className="text-muted">Ask about each sign and tick only those present. Nothing here is ticked for you; AI or document mentions are only reminders.</p>
        {answerHints("red_flag_screen_completed")}
        {bodyMap && bodyMap.regions.length > 0 && (
          <p className="mt-2 text-sm">
            <SourceTag kind="patient_reported" detail={`Body map saved by the ${roleWords(bodyMap.recorded_by_role)}`} /> Pointed to:{" "}
            <strong>{bodyMap.regions.map(labelOf).join(", ")}</strong>
          </p>
        )}
        <fieldset id="f-red_flags_present" className="mt-3">
          <legend className="sr-only">Red flags present</legend>
          <div className="grid gap-1 sm:grid-cols-2">
            {ATP_FLAGS.map(([k, l]) => (
              <label key={k} className="flex min-h-11 items-start gap-3 rounded px-2 py-2 hover:bg-primary-tint">
                <input
                  type="checkbox"
                  className="mt-1 size-5"
                  checked={form.red_flags_present.includes(k)}
                  onChange={(e) => set("red_flags_present", e.target.checked ? [...form.red_flags_present, k] : form.red_flags_present.filter((x) => x !== k))}
                />
                {l}
              </label>
            ))}
          </div>
        </fieldset>
        <div id="f-red_flag_screen_completed" className={`mt-4 rounded-lg border-2 p-3 ${errors.red_flag_screen_completed ? "border-error" : "border-primary"}`}>
          <label className="flex min-h-11 items-start gap-3 font-bold">
            <input type="checkbox" className="mt-1 size-5" checked={form.red_flag_screen_completed} onChange={(e) => set("red_flag_screen_completed", e.target.checked)} />I asked about every sign above with the patient
          </label>
          {errors.red_flag_screen_completed && <p className="font-bold text-error">{errors.red_flag_screen_completed}</p>}
        </div>
      </Card>

      {(docs?.values.length ?? 0) > 0 && (
        <Card>
          <h2 className="text-xl font-bold">Checked lab report values (for reference)</h2>
          <p className="text-muted">Lab values are shown to the clinician. They are not triage rule inputs and never change the urgency.</p>
          <ul className="mt-2 space-y-1">
            {docs!.values.map((v) => (
              <li key={v.field_id} className="flex flex-wrap items-center gap-2">
                <strong>{v.name}</strong> {String((v.value as { value?: unknown }).value ?? "")} {String((v.value as { unit?: unknown }).unit ?? "")}
                <SourceTag kind={v.source.type === "ocr_manual_correction" ? "document_corrected" : "document"} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      {otherAnswers.length > 0 && (
        <Card>
          <h2 className="text-xl font-bold">Other follow-up answers</h2>
          <p className="text-muted">Notes on this device for the clinician. They are not triage rule inputs.</p>
          <ul className="mt-2 space-y-1">
            {otherAnswers.map(([name, a]) => (
              <li key={name}>
                {a.question}: <strong>{a.answer}</strong>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit">Continue to final check</Button>
        <span className="text-sm text-muted">
          {VITALS.filter((v) => filled(v.key)).length} of {VITALS.length} measurements entered
        </span>
      </div>
    </form>,
  );
}

export default function ReviewPage() {
  return (
    <Suspense>
      <ReviewScreen />
    </Suspense>
  );
}
