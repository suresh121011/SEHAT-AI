"use client";

// Referral closure tracking in the case workspace (docs/21; docs/05 §5.1). The medical officer refers a signed-off case
// with closed-list choices only, then records each step to closure. The letter and the FHIR export are built by the
// server from the signed-off triage run. SEHAT does not call the ambulance or notify the receiving facility.
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { Icon } from "@/components/Icon";
import { StatusPill, UrgencyBadge } from "@/components/review/Badges";
import { SectionCard } from "@/components/review/Panel";
import { Button, Field, Notice, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  BLOCKED_WORDS,
  EMPTY_FORM,
  NEXT_STEP_WORDS,
  detailKind,
  fhirFileName,
  formErrors,
  sinceText,
  toRequest,
  vitalLine,
  type CaseReferrals,
  type Option,
  type Referral,
  type ReferralForm,
  type ReferralLetter,
  type ReferralOptions,
  type ReferralStatus,
} from "@/lib/referral";
import { fieldWords } from "@/lib/review";

const TERMINAL: ReferralStatus[] = ["outcome_recorded", "not_reached"];

function problem(err: unknown): string {
  return err instanceof ApiError ? err.message : "The server could not be reached. Try again.";
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function printArticle(article: HTMLElement, footer: string) {
  document.getElementById("print-root")?.remove();
  const root = document.createElement("div");
  root.id = "print-root";
  root.appendChild(article.cloneNode(true));
  const foot = document.createElement("p");
  foot.className = "print-footer";
  foot.textContent = footer;
  root.appendChild(foot);
  document.body.appendChild(root);
  const html = document.documentElement;
  html.classList.add("print-note");
  const done = () => {
    html.classList.remove("print-note");
    root.remove();
  };
  window.addEventListener("afterprint", done, { once: true });
  window.print();
}

export function ReferralPanel({ caseId }: { caseId: string }) {
  const [data, setData] = useState<CaseReferrals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setData(await api.get<CaseReferrals>(`cases/${caseId}/referrals`, { signal }));
        setError(null);
      } catch (err) {
        if (!signal?.aborted) setError(problem(err));
      }
    },
    [caseId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const newestFirst = data ? [...data.referrals].reverse() : [];

  return (
    <SectionCard id={`referral-${caseId}`} title="Referral" icon="send">
      {error && (
        <Notice tone="error" role="alert">
          {error}
        </Notice>
      )}
      {!data && !error && <p className="text-sm text-muted">Loading referrals…</p>}
      {data &&
        newestFirst.map((r) => <ReferralCard key={r.referral_id} caseId={caseId} referral={r} options={data.options} onChanged={() => void load()} />)}
      {data?.can_refer && !showForm && (
        <Button onClick={() => setShowForm(true)}>
          <Icon name="send" size={16} /> Refer this patient
        </Button>
      )}
      {data?.can_refer && showForm && data.options && data.latest_triage_run_id && (
        <ReferralFormView
          caseId={caseId}
          runId={data.latest_triage_run_id}
          options={data.options}
          onDone={() => {
            setShowForm(false);
            void load();
          }}
          onCancel={() => setShowForm(false)}
        />
      )}
      {data && !data.can_refer && data.referrals.length === 0 && data.refer_blocked && (
        <p className="text-sm text-muted">{BLOCKED_WORDS[data.refer_blocked] ?? "This case cannot be referred now."}</p>
      )}
    </SectionCard>
  );
}

function Choice({ name, legend, options, value, onChange, error }: { name: string; legend: string; options: Option[]; value: string; onChange: (v: string) => void; error?: string }) {
  return (
    <fieldset className="space-y-1" aria-describedby={error ? `${name}-error` : undefined}>
      <legend className="text-sm font-bold text-ink">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <label key={o.code} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm ${value === o.code ? "border-strong bg-surface-1 font-bold" : "border-subtle"}`}>
            <input type="radio" name={name} value={o.code} checked={value === o.code} onChange={() => onChange(o.code)} />
            {o.label}
          </label>
        ))}
      </div>
      {error && (
        <p id={`${name}-error`} className="text-sm text-error">
          {error}
        </p>
      )}
    </fieldset>
  );
}

function Select({ id, label, options, value, onChange, error, hint }: { id: string; label: string; options: Option[]; value: string; onChange: (v: string) => void; error?: string; hint?: string }) {
  return (
    <Field id={id} label={label} error={error} hint={hint}>
      <select id={id} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {options.map((o) => (
          <option key={o.code} value={o.code}>
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

function ReferralFormView({ caseId, runId, options, onDone, onCancel }: { caseId: string; runId: string; options: ReferralOptions; onDone: () => void; onCancel: () => void }) {
  const [form, setForm] = useState<ReferralForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof ReferralForm, string>>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof ReferralForm) => (value: string) => setForm((f) => ({ ...f, [key]: value }));
  const destinations = options.destinations.map((d) => ({ code: d.code, label: d.name }));

  async function submit() {
    const found = formErrors(form);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    setFailure(null);
    try {
      await api.post(`cases/${caseId}/referrals`, toRequest(form, runId));
      onDone();
    } catch (err) {
      setFailure(problem(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="space-y-3 rounded-lg border border-subtle p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="text-sm font-bold">New referral (from the signed-off triage)</p>
      <Select id={`ref-to-${caseId}`} label="Refer to" options={destinations} value={form.to_facility} onChange={set("to_facility")} error={errors.to_facility} hint={options.facility_list_note} />
      <Select id={`ref-reason-${caseId}`} label="Reason for referral" options={options.reasons} value={form.reason_code} onChange={set("reason_code")} error={errors.reason_code} />
      <Choice name={`ref-transport-${caseId}`} legend="Transport" options={options.transport} value={form.transport_mode} onChange={set("transport_mode")} error={errors.transport_mode} />
      <Choice
        name={`ref-escort-${caseId}`}
        legend="Someone must go with the patient"
        options={[
          { code: "yes", label: "Escort required" },
          { code: "no", label: "Not required" },
        ]}
        value={form.escort}
        onChange={(v) => set("escort")(v === "yes" ? "yes" : "no")}
        error={errors.escort}
      />
      <Field id={`ref-minutes-${caseId}`} label="Travel time in minutes (optional)" error={errors.travel_minutes}>
        <input id={`ref-minutes-${caseId}`} className={inputClass} inputMode="numeric" value={form.travel_minutes} onChange={(e) => set("travel_minutes")(e.target.value)} />
      </Field>
      <Select id={`ref-insurance-${caseId}`} label="Insurance scheme (as stated by the patient)" options={options.insurance} value={form.insurance_scheme} onChange={set("insurance_scheme")} error={errors.insurance_scheme} />
      <Notice tone="info">
        <p>SEHAT does not call the 108 ambulance or tell the receiving facility. Call them yourself and give the patient the printed letter.</p>
      </Notice>
      {failure && (
        <Notice tone="error" role="alert">
          {failure}
        </Notice>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          <Icon name="send" size={16} /> {busy ? "Saving…" : "Create referral"}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ReferralCard({ caseId, referral: r, options, onChanged }: { caseId: string; referral: Referral; options?: ReferralOptions; onChanged: () => void }) {
  const [pending, setPending] = useState<ReferralStatus | null>(null);
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [letter, setLetter] = useState<ReferralLetter | null>(null);
  const letterRef = useRef<HTMLElement>(null);
  const closed = TERMINAL.includes(r.status);
  const tone = r.overdue ? "attention" : closed ? "done" : "neutral";

  async function record(status: ReferralStatus, code: string | null) {
    setBusy(true);
    setFailure(null);
    try {
      await api.post(`cases/${caseId}/referrals/${r.referral_id}/status`, { status, expected_status: r.status, detail_code: code, confirm: true });
      setPending(null);
      setDetail("");
      onChanged();
    } catch (err) {
      setFailure(problem(err));
      if (err instanceof ApiError && err.code === "STALE_REFERRAL_STATUS") onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function showLetter() {
    setFailure(null);
    try {
      setLetter(await api.get<ReferralLetter>(`cases/${caseId}/referrals/${r.referral_id}/letter`));
    } catch (err) {
      setFailure(problem(err));
    }
  }

  async function downloadFhir() {
    setFailure(null);
    try {
      const bundle = await api.get<Record<string, unknown>>(`cases/${caseId}/referrals/${r.referral_id}/fhir`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: "application/fhir+json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = fhirFileName(r);
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000); // revoking at once can cancel the download in some browsers
    } catch (err) {
      setFailure(problem(err));
    }
  }

  const detailOptions = pending ? (detailKind(pending) === "outcome" ? options?.outcomes : options?.barriers) ?? [] : [];

  return (
    <article className="space-y-2 rounded-lg border border-subtle p-3" aria-label={`Referral to ${r.to_facility.name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-bold">
          To {r.to_facility.name} <span className="font-normal text-muted">from {r.from_facility.name}</span>
        </p>
        <div className="flex items-center gap-2">
          <UrgencyBadge urgency={r.urgency} size="sm" />
          <StatusPill tone={tone} icon={r.overdue ? "clock" : closed ? "check" : "send"}>
            {r.overdue ? "Overdue" : r.status_label}
          </StatusPill>
        </div>
      </div>
      <p className="text-sm text-muted">
        Referred {when(r.created_at)} · {sinceText(r.hours_since_update, r.overdue, r.overdue_threshold_hours)}
      </p>
      {r.reason && (
        <ul className="space-y-0.5 text-sm">
          <li>Reason: {r.reason.label}</li>
          {r.transport && (
            <li>
              Transport: {r.transport.label} · {r.transport.escort_required ? "escort required" : "no escort required"}
              {r.transport.travel_minutes ? ` · about ${r.transport.travel_minutes} min` : ""}
            </li>
          )}
          {r.insurance && <li>Insurance: {r.insurance.label}</li>}
        </ul>
      )}
      {r.status_history && (
        <ol className="space-y-0.5 border-l-2 border-subtle pl-3 text-sm" aria-label="Status history">
          {r.status_history.map((h) => (
            <li key={h.status}>
              <span className="font-bold">{h.label}</span>
              {h.detail_label ? `: ${h.detail_label}` : ""} <span className="text-muted">· {when(h.at)}</span>
            </li>
          ))}
        </ol>
      )}
      {failure && (
        <Notice tone="error" role="alert">
          {failure}
        </Notice>
      )}
      {(r.allowed_next ?? []).length > 0 && !pending && (
        <div className="flex flex-wrap gap-2">
          {(r.allowed_next ?? []).map((s) => (
            <Button key={s} variant="secondary" disabled={busy} onClick={() => (detailKind(s) ? setPending(s) : void record(s, null))}>
              {NEXT_STEP_WORDS[s]}
            </Button>
          ))}
        </div>
      )}
      {pending && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-56">
            <Select id={`detail-${r.referral_id}`} label={detailKind(pending) === "outcome" ? "Outcome" : "What stopped the patient"} options={detailOptions} value={detail} onChange={setDetail} />
          </div>
          <Button disabled={busy || !detail} onClick={() => void record(pending, detail)}>
            {NEXT_STEP_WORDS[pending]}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setPending(null)}>
            Cancel
          </Button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" aria-expanded={letter !== null} onClick={() => void (letter ? setLetter(null) : showLetter())}>
          <Icon name="document" size={16} /> {letter ? "Hide letter" : "View letter"}
        </Button>
        <Button variant="secondary" onClick={() => void downloadFhir()}>
          <Icon name="list" size={16} /> Download FHIR R4 (JSON)
        </Button>
      </div>
      {letter && (
        <div className="space-y-2">
          <div className="no-print flex justify-end">
            <Button
              variant="secondary"
              onClick={() => letterRef.current && printArticle(letterRef.current, `Printed ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC · referral ${letter.referral_id} · ${letter.disclaimer}`)}
            >
              <Icon name="print" size={16} /> Print letter
            </Button>
          </div>
          <LetterView letter={letter} articleRef={letterRef} />
        </div>
      )}
    </article>
  );
}

function LetterView({ letter: l, articleRef }: { letter: ReferralLetter; articleRef: RefObject<HTMLElement | null> }) {
  const age = l.patient.age_years !== null ? `${l.patient.age_years} years` : "Not recorded";
  return (
    <article ref={articleRef} className="space-y-2 rounded-lg border border-subtle bg-card p-4 text-sm">
      <h4 className="flex items-center justify-between gap-2 text-lg font-bold">
        {l.title} <UrgencyBadge urgency={l.urgency.level} size="sm" />
      </h4>
      <p>
        From <strong>{l.from_facility.name}</strong> to <strong>{l.to_facility.name}</strong> · referred {when(l.created_at)}
      </p>
      <p>
        Patient token: <strong>{l.patient_token}</strong> · Age: {age}
        {l.patient.pregnant ? ` · Pregnant${l.patient.trimester ? `, trimester ${l.patient.trimester}` : ""}` : ""} · Visit type: {l.scenario.label}
      </p>
      <h5 className="font-bold">Urgency</h5>
      <p>
        {l.urgency.level}: {l.urgency.meaning}
        {l.urgency.source === "reviewer_raise" ? ` · raised by a reviewer from ${l.urgency.rules_urgency} (the protocol rules)` : " · from the protocol rules"}
      </p>
      {l.urgency.reviewer_recorded && (
        <p>
          A reviewer recorded {l.urgency.reviewer_recorded}. A reviewer can raise urgency but not lower it, so the referral keeps {l.urgency.level}.
        </p>
      )}
      <h5 className="font-bold">Reason for referral</h5>
      <p>{l.reason.label}</p>
      <h5 className="font-bold">Rules that set the urgency</h5>
      {l.flags.length === 0 ? (
        <p>None</p>
      ) : (
        <ul className="list-disc space-y-1 pl-5">
          {l.flags.map((f) => (
            <li key={f.rule_id}>
              {f.reason} ({f.evidence.map((e) => `${fieldWords(e.field)} ${String(e.value)}, threshold ${e.threshold}`).join("; ")})
              <br />
              <span className="text-muted">Source: {f.source}</span>
            </li>
          ))}
        </ul>
      )}
      <h5 className="font-bold">Vital signs (recorded at triage)</h5>
      {l.vitals.length === 0 ? (
        <p>None recorded</p>
      ) : (
        <ul className="list-disc pl-5">
          {l.vitals.map((v) => (
            <li key={v.field}>{vitalLine(v)}</li>
          ))}
        </ul>
      )}
      {l.missing_fields.length > 0 && <p>Not recorded: {l.missing_fields.map(fieldWords).join(", ")}</p>}
      <h5 className="font-bold">Transport</h5>
      <p>
        {l.transport.label} · {l.transport.escort_required ? "escort required" : "no escort required"}
        {l.transport.travel_minutes ? ` · about ${l.transport.travel_minutes} min` : ""} · Insurance: {l.insurance.label}
      </p>
      <h5 className="font-bold">Sign-off</h5>
      <p>
        {l.sign_off.statement} · {when(l.sign_off.signed_off_at)}
      </p>
      <p className="text-xs text-muted">{l.disclaimer}</p>
    </article>
  );
}
