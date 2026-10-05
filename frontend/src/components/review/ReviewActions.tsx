"use client";

// Reviewer actions, each a deliberate, confirmed, server-recorded step (app/review_queue.py):
// - Sign off (PATCH /triage/{id}/sign-off, confirm: true) — records that this run was reviewed; not a diagnosis.
// - Override (PATCH /triage/{id}/override) — reason code from the server's list; the rules result is kept. Sends the
//   urgency the reviewer saw (expected_urgency); if another reviewer changed it meanwhile the server answers 409.
// - Acknowledge RED (POST /triage/{id}/acknowledge) — "seen", not "reviewed".
// - Correct vitals (POST /triage/{id}/corrections) — a NEW rules-engine run on corrected input, with a reason code;
//   the earlier run and its review stay.
// Every action is bound to the run the reviewer is looking at (triage_run_id); a newer run returns 409, which is
// shown, never retried silently. Success text comes only from the server's response. The browser never computes
// urgency: the correction result is whatever the server's rules engine returned.
import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { UrgencyBadge } from "@/components/review/Badges";
import { Button, Notice, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { FAILURE_TITLE, buildSummary, failureKind, fieldMessage, mapServerErrors, serverFieldErrors, type FailureKind, type MappedErrors, type SummaryItem } from "@/lib/formErrors";
import {
  EDITABLE_VITALS,
  applyVitalEdits,
  correctionFieldControl,
  correctionSuccess,
  fieldWords,
  overrideFieldControl,
  type CaseReview,
  type CorrectionResponse,
  type EditableVital,
  type ReasonOption,
  type Urgency,
  type VitalEdits,
} from "@/lib/review";

export type ActionDone = { kind: "sign_off" | "override" | "acknowledge" | "retriage"; message: string };
type Latest = NonNullable<CaseReview["latest"]>;

/** Conflicts after which the case is reloaded (the reviewer was looking at an out-of-date view). */
const RELOAD_CODES = new Set(["STALE_TRIAGE_RUN", "URGENCY_CHANGED"]);

type Failure = { kind: FailureKind; title: string; message: string; server: MappedErrors | null };

function describe(err: unknown, resolve: ((field: string) => string | null) | null, overrides: Record<string, string>): Failure {
  if (!(err instanceof ApiError)) {
    return { kind: "network", title: FAILURE_TITLE.network, message: "The server could not be reached. Nothing is known to have been recorded; refresh before trying again.", server: null };
  }
  const ref = err.requestId ? ` (reference ${err.requestId})` : "";
  const kind = failureKind(err.status, err.code);
  const known: Record<string, string> = {
    STALE_TRIAGE_RUN: "Out of date: a newer triage run exists for this case. Nothing was recorded. The case has been reloaded; review the latest run.",
    URGENCY_CHANGED: "Out of date: another reviewer changed the recorded urgency while this was open. Nothing was recorded. The case has been reloaded; check the current urgency before deciding again.",
    ALREADY_SIGNED_OFF: "This run is already signed off. Nothing new was recorded.",
    ALREADY_ACKNOWLEDGED: "This RED case was already acknowledged. Nothing new was recorded.",
    IMAGE_FINDINGS_NOT_REVIEWED:
      "Medical image findings on this case have not been marked as reviewed. Open Source evidence → Medical image findings, look at each original image and tick \u201cFindings reviewed\u201d, then sign off. Nothing was recorded.",
    NO_CHANGE: "The new urgency is the same as the current one. Nothing was recorded.",
    NOT_ESCALATED: "This run is not RED, so there is nothing to acknowledge.",
    CONSENT_REQUIRED: "Consent for triage is not in effect, so this cannot be recorded.",
    PII_DETECTED: "The explanation looks like it contains a name, phone number or other identifier. Remove it and try again. Nothing was recorded.",
    FORBIDDEN: "Your account cannot do this. Only a medical officer can review.",
    VALIDATION_ERROR: "Some values were not accepted. They are marked below; your entries are kept. Nothing was recorded.",
    ...overrides,
  };
  let server: MappedErrors | null = null;
  if (err.code === "VALIDATION_ERROR" && resolve) server = mapServerErrors(serverFieldErrors(err.details), resolve);
  if (err.code === "PII_DETECTED" && resolve?.("reason_text")) server = { fields: { reason_text: "Looks like a name, phone number or other identifier. Remove it." }, unmapped: [] };
  const text = known[err.code] ?? (kind === "session" ? "Your session has ended. Sign in again; nothing was recorded." : err.message);
  return { kind, title: FAILURE_TITLE[kind], message: `${text}${ref}`, server };
}

function useSubmit(resolve: ((field: string) => string | null) | null = null, overrides: Record<string, string> = {}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  async function run(fn: () => Promise<void>, onStale: () => void): Promise<boolean> {
    if (busy) return false; // no double submission
    setBusy(true);
    setFailure(null);
    try {
      await fn();
      return true;
    } catch (err) {
      setFailure(describe(err, resolve, overrides));
      if (err instanceof ApiError && RELOAD_CODES.has(err.code)) onStale();
      return false;
    } finally {
      setBusy(false);
    }
  }
  /** The reviewer edited a field: its server message no longer applies to what is on screen. */
  function clearField(key: string) {
    setFailure((f) => {
      if (!f?.server || !(key in f.server.fields)) return f;
      const fields = { ...f.server.fields };
      delete fields[key];
      return { ...f, server: { ...f.server, fields } };
    });
  }
  return { busy, failure, setFailure, run, clearField };
}

// Non-validation failures (conflict, authorization, server, network). When `focusKey` changes the notice takes focus:
// the submit button may have been disabled while busy, and without this focus would drop to <body> (Agent D, 409).
function FailureNotice({ failure, focusKey = 0 }: { failure: Failure | null; focusKey?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (failure && focusKey > 0) ref.current?.focus();
  }, [failure, focusKey]);
  if (!failure) return null;
  return (
    <div ref={ref} tabIndex={-1} className="outline-none focus-visible:ring-2 focus-visible:ring-focus">
      <Notice tone={failure.kind === "conflict" ? "warning" : "error"} role="alert" title={failure.title}>
        <p>{failure.message}</p>
      </Notice>
    </div>
  );
}

/** Error summary (GOV.UK / WAI pattern): role=alert, focused on a failed submit, each item moves focus to its control. */
function ErrorSummary({ id, items, idFor, focusKey, lead }: { id: string; items: SummaryItem[]; idFor: (key: string) => string; focusKey: number; lead?: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusKey > 0) ref.current?.focus();
  }, [focusKey]);
  if (items.length === 0 && !lead) return null;
  return (
    <div ref={ref} role="alert" tabIndex={-1} aria-labelledby={`${id}-title`} className="rounded border-2 border-error bg-error-bg p-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-error">
      <p id={`${id}-title`} className="font-bold text-error">
        Fix before recording:
      </p>
      {lead && <p className="mb-1">{lead}</p>}
      <ul className="list-disc pl-5">
        {items.map((it, i) => (
          <li key={`${it.key ?? "none"}-${i}`}>
            {it.key ? (
              <button type="button" className="text-left text-ink underline underline-offset-4" onClick={() => document.getElementById(idFor(it.key!))?.focus()}>
                {it.message}
              </button>
            ) : (
              it.message
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <p id={id} className="text-sm font-bold text-error">
      <span className="sr-only">Error: </span>
      {message}
    </p>
  );
}

const describedBy = (...ids: (string | false | undefined | null)[]) => ids.filter(Boolean).join(" ") || undefined;

const time = (iso: string) => new Date(iso).toLocaleTimeString();

// ── Sign-off ─────────────────────────────────────────────────────────────

export function SignOffDialog({ open, onClose, caseId, latest, onDone, onStale }: { open: boolean; onClose: () => void; caseId: string; latest: Latest; onDone: (d: ActionDone) => void; onStale: () => void }) {
  const [checked, setChecked] = useState(false);
  const s = useSubmit();
  const close = () => {
    setChecked(false);
    s.setFailure(null);
    onClose();
  };
  const submit = () =>
    s.run(async () => {
      const r = await api.patch<{ signed_off_at: string; reviewer_role: string; rules_urgency: Urgency; effective_urgency: Urgency }>(`triage/${caseId}/sign-off`, { triage_run_id: latest.triage_run_id, confirm: true });
      onDone({ kind: "sign_off", message: `Signed off at ${time(r.signed_off_at)} (server time) under your account. Rules result ${r.rules_urgency}; recorded urgency ${r.effective_urgency}.` });
      close();
    }, onStale);
  return (
    <ConfirmDialog
      open={open}
      onClose={close}
      labelledBy="signoff-title"
      title={<>Sign off review of {latest.patient_token}</>}
      footer={
        <>
          <Button variant="secondary" data-autofocus onClick={close}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!checked || s.busy} aria-disabled={!checked || s.busy}>
            <Icon name="check" size={16} /> {s.busy ? "Recording…" : "Sign off this run"}
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        Rules result <UrgencyBadge urgency={latest.rules_urgency} size="sm" />
        {latest.effective_urgency !== latest.rules_urgency && (
          <>
            · reviewer-recorded <UrgencyBadge urgency={latest.effective_urgency} size="sm" />
          </>
        )}
      </div>
      <Notice tone="warning" title="What sign-off means">
        <p>It records that you reviewed this triage run. It is not a diagnosis and does not mean the patient is safe.</p>
      </Notice>
      {latest.missing_fields.length > 0 && (
        <p className="text-sm">
          <strong>Still missing:</strong> {latest.missing_fields.map(fieldWords).join(", ")}. They stay missing after sign-off; they are never treated as normal.
        </p>
      )}
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} className="mt-1 size-5 accent-primary" />
        <span>I have reviewed the rules result, the recorded inputs and the available evidence for this triage run.</span>
      </label>
      <p className="text-xs text-muted">Recorded with your account and server time, append-only. Run {latest.triage_run_id.slice(0, 8)}.</p>
      <FailureNotice failure={s.failure} />
    </ConfirmDialog>
  );
}

// ── Override ─────────────────────────────────────────────────────────────

const LEVELS: Urgency[] = ["RED", "YELLOW", "GREEN"];
const RANK: Record<Urgency, number> = { RED: 3, YELLOW: 2, GREEN: 1 };
const OVERRIDE_ORDER = ["new_urgency", "reason_code", "reason_text", "confirm"];
const OVERRIDE_LABEL: Record<string, string> = { new_urgency: "Urgency", reason_code: "Reason code", reason_text: "Explanation", confirm: "Confirmation" };

export function OverrideDialog({ open, onClose, caseId, latest, reasons, onDone, onStale }: { open: boolean; onClose: () => void; caseId: string; latest: Latest; reasons: CaseReview["override_reasons"]; onDone: (d: ActionDone) => void; onStale: () => void }) {
  const current = latest.effective_urgency;
  const options = LEVELS.filter((l) => l !== current);
  const [next, setNext] = useState<Urgency | "">("");
  const [code, setCode] = useState("");
  const [text, setText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [touched, setTouched] = useState(false);
  const [focusKey, setFocusKey] = useState(0);
  // The case was reloaded (another reviewer acted, or a newer run): the choice was made against old values.
  const [seen, setSeen] = useState(`${latest.triage_run_id}|${current}`);
  if (seen !== `${latest.triage_run_id}|${current}`) {
    setSeen(`${latest.triage_run_id}|${current}`);
    setNext("");
    setConfirmed(false);
  }
  const s = useSubmit(overrideFieldControl);
  // After a conflict the dialog reloads from the server; old "fix before recording" items no longer apply (Agent D).
  useEffect(() => {
    if (s.failure?.kind === "conflict") setTouched(false);
  }, [s.failure]);
  const reason = reasons.find((r) => r.code === code);
  const textTrim = text.trim();
  const client: Record<string, string> = {};
  if (!next) client.new_urgency = "Choose the urgency you are recording.";
  if (!code) client.reason_code = "Choose a reason code.";
  if (reason?.requires_text && textTrim.length < 10) client.reason_text = "Explain the reason in at least 10 characters (required for this reason).";
  if (!confirmed) client.confirm = "Tick the confirmation.";
  const shownClient = touched ? client : {};
  const server = s.failure?.server ?? null;
  const summary = buildSummary(OVERRIDE_ORDER, shownClient, server, (k) => OVERRIDE_LABEL[k] ?? k);
  const msg = (k: string) => fieldMessage(k, shownClient, server);
  const idFor = (k: string) => (k === "new_urgency" ? `override-level-${options[0]}` : k === "reason_code" ? "override-reason" : k === "reason_text" ? "override-text" : "override-confirm");
  const close = () => {
    setNext("");
    setCode("");
    setText("");
    setConfirmed(false);
    setTouched(false);
    s.setFailure(null);
    onClose();
  };
  const submit = async () => {
    setTouched(true);
    if (Object.keys(client).length) {
      s.setFailure(null);
      setFocusKey((k) => k + 1);
      return;
    }
    const ok = await s.run(async () => {
      const r = await api.patch<{ old_urgency: Urgency; new_urgency: Urgency; overridden_at: string; rules_urgency: Urgency }>(`triage/${caseId}/override`, {
        triage_run_id: latest.triage_run_id,
        expected_urgency: current,
        new_urgency: next,
        reason_code: code,
        reason_text: textTrim || null,
        confirm: true,
      });
      onDone({ kind: "override", message: `Override recorded at ${time(r.overridden_at)} (server time): ${r.old_urgency} → ${r.new_urgency}. The rules result (${r.rules_urgency}) is kept unchanged.` });
      close();
    }, onStale);
    if (!ok) setFocusKey((k) => k + 1);
  };
  const lowering = next !== "" && RANK[next] < RANK[current];
  return (
    <ConfirmDialog
      open={open}
      onClose={close}
      labelledBy="override-title"
      title={<>Override urgency for {latest.patient_token}</>}
      footer={
        <>
          <Button variant="secondary" data-autofocus onClick={close}>
            Cancel
          </Button>
          <Button variant="danger" onClick={submit} disabled={s.busy}>
            <Icon name="flag" size={16} /> {s.busy ? "Recording…" : "Record override"}
          </Button>
        </>
      }
    >
      <ErrorSummary id="override-errors" items={summary} idFor={idFor} focusKey={focusKey} lead={s.failure?.kind === "validation" ? s.failure.message : null} />
      <p className="text-sm">An override is your clinical judgement, recorded beside the rules result. It is not a data correction — to fix a wrong value, use “Correct vitals” instead.</p>
      <fieldset className="grid gap-3 sm:grid-cols-2">
        <legend className="sr-only">Current and proposed urgency</legend>
        <div className="rounded border border-subtle p-3">
          <p className="text-xs font-bold uppercase text-muted">Current</p>
          <UrgencyBadge urgency={current} />
          <p className="mt-1 text-xs text-muted">Rules engine: {latest.rules_urgency} (kept as recorded)</p>
        </div>
        <div className={`rounded border-2 border-dashed p-3 ${msg("new_urgency") ? "border-error" : "border-human"}`}>
          <p id="override-level-label" className="text-xs font-bold uppercase text-muted">
            You record
          </p>
          <FieldError id="override-level-err" message={msg("new_urgency")} />
          <div
            className="mt-1 flex flex-wrap gap-2"
            role="radiogroup"
            aria-labelledby="override-level-label"
            aria-invalid={msg("new_urgency") ? true : undefined}
            aria-describedby={describedBy(msg("new_urgency") && "override-level-err")}
          >
            {options.map((l) => (
              <label key={l} className="flex min-h-10 cursor-pointer items-center gap-1.5">
                <input
                  id={`override-level-${l}`}
                  type="radio"
                  name="override-level"
                  value={l}
                  checked={next === l}
                  onChange={() => {
                    setNext(l);
                    s.clearField("new_urgency");
                  }}
                  className="size-4 accent-primary"
                />
                <UrgencyBadge urgency={l} size="sm" />
              </label>
            ))}
          </div>
        </div>
      </fieldset>
      {lowering && (
        <Notice tone="warning">
          <p>Lowering is recorded, but the case keeps its rules-engine position in the queue until it is signed off. The rules result stays visible.</p>
        </Notice>
      )}
      <ReasonFields
        idPrefix="override"
        reasons={reasons}
        code={code}
        text={text}
        maxLength={500}
        onCode={(v) => {
          setCode(v);
          s.clearField("reason_code");
        }}
        onText={(v) => {
          setText(v);
          s.clearField("reason_text");
        }}
        codeError={msg("reason_code")}
        textError={msg("reason_text")}
        hint="Prototype list from the server, pending clinical governance review."
      />
      <ConfirmBox id="override-confirm" checked={confirmed} onChange={setConfirmed} error={msg("confirm")}>
        I confirm this override. It will be logged with my account and server time and cannot be edited or deleted.
      </ConfirmBox>
      <FailureNotice failure={s.failure && s.failure.kind !== "validation" ? s.failure : null} focusKey={focusKey} />
    </ConfirmDialog>
  );
}

function ReasonFields({
  idPrefix,
  reasons,
  code,
  text,
  maxLength,
  onCode,
  onText,
  codeError,
  textError,
  hint,
}: {
  idPrefix: string;
  reasons: ReasonOption[];
  code: string;
  text: string;
  maxLength: number;
  onCode: (v: string) => void;
  onText: (v: string) => void;
  codeError: string | undefined;
  textError: string | undefined;
  hint: string;
}) {
  const reason = reasons.find((r) => r.code === code);
  const codeId = `${idPrefix}-reason`;
  const textId = `${idPrefix}-text`;
  return (
    <>
      <div className={`space-y-1 ${codeError ? "border-l-4 border-error pl-2" : ""}`}>
        <label htmlFor={codeId} className="block font-bold">
          Reason code <span className="font-normal text-muted">(required)</span>
        </label>
        <FieldError id={`${codeId}-err`} message={codeError} />
        <select
          id={codeId}
          value={code}
          onChange={(e) => onCode(e.target.value)}
          className={inputClass}
          aria-invalid={codeError ? true : undefined}
          aria-describedby={describedBy(`${codeId}-hint`, codeError && `${codeId}-err`)}
        >
          <option value="">Choose a reason…</option>
          {reasons.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
        <p id={`${codeId}-hint`} className="text-xs text-muted">
          {reasons.length === 0 ? "The server sent no reason list; this cannot be recorded until it does." : hint}
        </p>
      </div>
      <div className={`space-y-1 ${textError ? "border-l-4 border-error pl-2" : ""}`}>
        <label htmlFor={textId} className="block font-bold">
          Explanation <span className="font-normal text-muted">({reason?.requires_text ? "required, at least 10 characters" : "optional"}; no names or phone numbers)</span>
        </label>
        <FieldError id={`${textId}-err`} message={textError} />
        <textarea
          id={textId}
          value={text}
          maxLength={maxLength}
          onChange={(e) => onText(e.target.value)}
          rows={3}
          className={inputClass}
          aria-invalid={textError ? true : undefined}
          aria-describedby={describedBy(`${textId}-count`, textError && `${textId}-err`)}
        />
        <p id={`${textId}-count`} className="text-xs text-muted">
          {text.length}/{maxLength}
        </p>
      </div>
    </>
  );
}

function ConfirmBox({ id, checked, onChange, error, children }: { id: string; checked: boolean; onChange: (v: boolean) => void; error: string | undefined; children: React.ReactNode }) {
  return (
    <div className={error ? "border-l-4 border-error pl-2" : ""}>
      <FieldError id={`${id}-err`} message={error} />
      <label className="flex items-start gap-2">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="mt-1 size-5 accent-primary"
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(error && `${id}-err`)}
        />
        <span>{children}</span>
      </label>
    </div>
  );
}

// ── RED acknowledgment ───────────────────────────────────────────────────

export function AcknowledgeButton({ caseId, latest, onDone, onStale }: { caseId: string; latest: Latest; onDone: (d: ActionDone) => void; onStale: () => void }) {
  const s = useSubmit();
  const ack = () =>
    s.run(async () => {
      const r = await api.post<{ acknowledged_at: string; acknowledged_late: boolean; notification_sent: boolean }>(`triage/${caseId}/acknowledge`, { triage_run_id: latest.triage_run_id });
      const mins = (latest.escalation?.window_seconds ?? 0) / 60;
      onDone({ kind: "acknowledge", message: `RED case acknowledged at ${time(r.acknowledged_at)} (server time)${r.acknowledged_late ? `, after the ${mins}-minute target` : `, within the ${mins}-minute target`}. This records that you have seen it; the case still needs review and sign-off.` });
    }, onStale);
  return (
    <div className="space-y-1">
      {/* The caveat sits ABOVE the button so it is read before clicking (final council, Outsider). */}
      <p id="ack-caveat" className="text-sm font-bold">Acknowledging records only that you have seen this RED case. It is not a review: sign-off is still required, and the case stays RED in the queue until then.</p>
      <Button onClick={ack} disabled={s.busy} aria-describedby="ack-caveat" className="border-2 !border-white !bg-esc-overdue text-white hover:!bg-[#6f1713]">
        <Icon name="eye" size={16} /> {s.busy ? "Recording…" : "Acknowledge RED: seen, sign-off still needed"}
      </Button>
      <FailureNotice failure={s.failure} />
    </div>
  );
}

// ── Correct vitals → new rules-engine run ────────────────────────────────

const CORRECTION_TEXT_MAX = 300;
const VITAL_LABEL = Object.fromEntries(EDITABLE_VITALS.map((v) => [v.key, v.label])) as Record<EditableVital, string>;
const CORRECT_ORDER = [...EDITABLE_VITALS.map((v) => v.key as string), "reason_code", "reason_text", "confirm"];
const CORRECT_LABEL: Record<string, string> = { ...VITAL_LABEL, reason_code: "Reason code", reason_text: "Explanation", confirm: "Confirmation" };
const CORRECTION_MESSAGES: Record<string, string> = {
  NO_CHANGE: "These values are the same as the recorded run, so no correction was recorded. To confirm the recorded values, sign off instead.",
  NO_STORED_INPUT: "The original values for this run were not saved, so they cannot be corrected here. Nothing was recorded.",
  SCENARIO_MISMATCH: "The corrected input does not match this case's scenario. Nothing was recorded; reload the case and try again.",
  STALE_TRIAGE_RUN: "Out of date: a newer triage run exists for this case. Nothing was recorded. The case has been reloaded and the boxes now show the latest recorded values; check them and submit again only if a correction is still needed.",
};

export function CorrectVitalsDialog({
  open,
  onClose,
  caseId,
  latest,
  reasons,
  onDone,
  onStale,
}: {
  open: boolean;
  onClose: () => void;
  caseId: string;
  latest: Latest;
  reasons: ReasonOption[];
  onDone: (d: ActionDone) => void;
  onStale: () => void;
}) {
  const stored = (latest.input ?? {}) as Record<string, unknown>;
  const vitals = (stored.vitals ?? {}) as Record<string, unknown>;
  const initial = Object.fromEntries(EDITABLE_VITALS.map((v) => [v.key, vitals[v.key] === undefined || vitals[v.key] === null ? "" : String(vitals[v.key])])) as Record<EditableVital, string>;
  const [edits, setEdits] = useState<VitalEdits>(initial);
  const [code, setCode] = useState("");
  const [text, setText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [touched, setTouched] = useState(false);
  const [focusKey, setFocusKey] = useState(0);
  // After a reload onto a newer run, start again from that run's recorded values (the failure message is kept).
  const [seenRun, setSeenRun] = useState(latest.triage_run_id);
  if (seenRun !== latest.triage_run_id) {
    setSeenRun(latest.triage_run_id);
    setEdits(initial);
    setConfirmed(false);
    setTouched(false);
  }
  const s = useSubmit(correctionFieldControl, CORRECTION_MESSAGES);
  // After a conflict the dialog reloads from the server; old "fix before recording" items no longer apply (Agent D).
  useEffect(() => {
    if (s.failure?.kind === "conflict") setTouched(false);
  }, [s.failure]);
  const patched = applyVitalEdits(stored, edits);
  const changed = patched.ok ? patched.changed : [];
  const reason = reasons.find((r) => r.code === code);
  const textTrim = text.trim();

  const client: Record<string, string> = {};
  if (!patched.ok) {
    for (const [k, m] of Object.entries(patched.errors)) client[k] = m.startsWith(`${VITAL_LABEL[k as EditableVital]}: `) ? m.slice(VITAL_LABEL[k as EditableVital].length + 2) : m;
  }
  if (!code) client.reason_code = "Choose why the values are being corrected.";
  if (reason?.requires_text && textTrim.length < 10) client.reason_text = "Explain the reason in at least 10 characters (required for this reason).";
  if (textTrim.length > CORRECTION_TEXT_MAX) client.reason_text = `At most ${CORRECTION_TEXT_MAX} characters.`;
  if (!confirmed) client.confirm = "Tick the confirmation.";
  const noChange = patched.ok && changed.length === 0;
  const shownClient = touched ? client : {};
  const server = s.failure?.server ?? null;
  const summary = buildSummary(CORRECT_ORDER, shownClient, server, (k) => CORRECT_LABEL[k] ?? k);
  if (touched && noChange) summary.unshift({ key: "spo2", message: "No value was changed. To confirm the recorded values, sign off instead." });
  const msg = (k: string) => fieldMessage(k, shownClient, server);
  const idFor = (k: string) => (k === "reason_code" ? "correct-reason" : k === "reason_text" ? "correct-text" : k === "confirm" ? "correct-confirm" : `cv-${k}`);

  const close = () => {
    setEdits(initial);
    setCode("");
    setText("");
    setConfirmed(false);
    setTouched(false);
    s.setFailure(null);
    onClose();
  };
  const submit = async () => {
    setTouched(true);
    if (!patched.ok || noChange || Object.keys(client).length) {
      s.setFailure(null);
      setFocusKey((k) => k + 1);
      return;
    }
    const ok = await s.run(async () => {
      const r = await api.post<CorrectionResponse>(`triage/${caseId}/corrections`, {
        expected_triage_run_id: latest.triage_run_id,
        input: patched.input,
        reason_code: code,
        reason_text: textTrim || null,
        confirm: true,
      });
      onDone({ kind: "retriage", message: correctionSuccess(r).message });
      close();
    }, onStale);
    if (!ok) setFocusKey((k) => k + 1);
  };
  return (
    <ConfirmDialog
      open={open}
      onClose={close}
      labelledBy="correct-title"
      title={<>Correct recorded vitals for {latest.patient_token}</>}
      footer={
        <>
          <Button variant="secondary" data-autofocus onClick={close}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={s.busy}>
            <Icon name="refresh" size={16} /> {s.busy ? "Running rules…" : `Re-run rules with ${changed.length} change${changed.length === 1 ? "" : "s"}`}
          </Button>
        </>
      }
    >
      <ErrorSummary id="correct-errors" items={summary} idFor={idFor} focusKey={focusKey} lead={s.failure?.kind === "validation" ? s.failure.message : null} />
      <p className="text-sm">
        Use this when a recorded value is wrong after checking the patient. The server’s rules engine runs again on the corrected input and creates a{" "}
        <strong>new</strong> triage run; the original run, its values and its review stay unchanged in the history. Leave a box blank if the value was not
        measured — it is then treated as missing, never as normal. Red flags, screens and other fields are re-sent exactly as recorded.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {EDITABLE_VITALS.map((v) => {
          const was = initial[v.key];
          const now = edits[v.key] ?? "";
          const id = `cv-${v.key}`;
          const err = msg(v.key);
          return (
            <div key={v.key} className={`space-y-1 ${err ? "border-l-4 border-error pl-2" : ""}`}>
              <label htmlFor={id} className="block text-sm font-bold">
                {v.label}
              </label>
              <FieldError id={`${id}-err`} message={err} />
              <input
                id={id}
                inputMode={v.decimal ? "decimal" : "numeric"}
                value={now}
                onChange={(e) => {
                  setEdits((x) => ({ ...x, [v.key]: e.target.value }));
                  s.clearField(v.key);
                }}
                aria-describedby={describedBy(`${id}-was`, err && `${id}-err`)}
                aria-invalid={err ? true : undefined}
                className={inputClass}
              />
              <p id={`${id}-was`} className="text-xs text-muted">
                Recorded: {was === "" ? "not recorded" : was}
                {now.trim() !== was && <strong className="text-ink"> → {now.trim() === "" ? "not recorded" : now.trim()}</strong>}
              </p>
            </div>
          );
        })}
      </div>
      <ReasonFields
        idPrefix="correct"
        reasons={reasons}
        code={code}
        text={text}
        maxLength={CORRECTION_TEXT_MAX}
        onCode={(v) => {
          setCode(v);
          s.clearField("reason_code");
        }}
        onText={(v) => {
          setText(v);
          s.clearField("reason_text");
        }}
        codeError={msg("reason_code")}
        textError={msg("reason_text")}
        hint="Why the recorded value was wrong. Stored with the correction; prototype list from the server, pending clinical governance review."
      />
      <ConfirmBox id="correct-confirm" checked={confirmed} onChange={setConfirmed} error={msg("confirm")}>
        I checked these values with the patient. A new rules-engine run will be recorded under my account with this reason; the earlier run is kept.
      </ConfirmBox>
      <FailureNotice failure={s.failure && s.failure.kind !== "validation" ? s.failure : null} focusKey={focusKey} />
    </ConfirmDialog>
  );
}
