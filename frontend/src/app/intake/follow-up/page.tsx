"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { SourceTag } from "@/components/Provenance";
import { Button, Card, Field, Notice, Spinner, inputClass } from "@/components/ui";
import { buildCorrection, correctionKindOf, initialCorrection, type CorrectionField, type CorrectionForm, type CorrectionKind } from "@/lib/aiCorrection";
import { ApiError, api } from "@/lib/api";
import { readNotes, writeNotes } from "@/lib/intakeStore";
import { hrefFor } from "@/lib/steps";
import { useCase } from "@/lib/useCase";

// Follow-up questions come only from the backend (Phase 6 AI extraction: missing information → a deterministic
// English question bank). There is no endpoint to submit answers: they are kept on this device and shown next to
// the matching field on the review step, where the health worker enters the value.

type Question = { field_name: string; label: string; question_text: string; response_options: string[]; is_danger_sign: boolean; translations: Record<string, string> };
type Extraction = {
  extraction_id: string;
  provider: string;
  provider_kind: string;
  provider_is_fake: boolean;
  follow_up_questions: Question[];
  missing_information: { field_name: string; label: string; is_danger_sign: boolean }[];
  fields: AiField[];
};
type AiField = {
  field_id: string;
  origin: "model" | "ocr_reviewed";
  field: string;
  kind: string;
  status: string;
  agreement: string | null;
  value: unknown;
  candidates: { value: unknown; passes: number; not_mentioned?: boolean }[];
  evidence: { quote?: string }[];
  needs_review: boolean;
  priority_review: boolean;
  review: { event_id: string; outcome: string; corrected?: { value?: unknown; value2?: unknown; unit?: unknown; negated?: unknown } | null } | null;
};

function show(v: unknown): string {
  if (v === null || v === undefined) return "no single value";
  if (typeof v !== "object") return String(v);
  const o = v as Record<string, unknown>;
  if ("flag" in o) return `${String(o.flag).replaceAll("_", " ")}${o.negated ? " (denied)" : ""}`;
  if ("name" in o && "negated" in o) return `${o.negated ? "denies " : ""}${o.name}`;
  if ("name" in o) return [o.name, o.dose, o.frequency].filter(Boolean).join(" ");
  if ("value" in o) return `${o.value}${o.value2 != null ? `/${o.value2}` : ""}${o.unit ? ` ${o.unit}` : ""}`;
  return JSON.stringify(v);
}

function correctedText(c: { value?: unknown; value2?: unknown; unit?: unknown; negated?: unknown }): string {
  if (typeof c.negated === "boolean" && c.value == null) return c.negated ? "denies it" : "has it";
  return [c.value != null ? `${c.value}${c.value2 != null ? `/${c.value2}` : ""}` : null, typeof c.unit === "string" ? c.unit : null].filter(Boolean).join(" ");
}

const OUTCOME_WORDS: Record<string, string> = { accepted: "Accepted", rejected: "Rejected", unsure: "Not sure", corrected: "Corrected" };

// Per-field decisions on AI-extracted values (POST /ai/fields/{id}/review). One field at a time, no bulk accept.
function AiFieldReview({ caseId, ext, onChanged }: { caseId: string; ext: Extraction; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [changing, setChanging] = useState<string | null>(null);
  const fields = ext.fields.filter((f) => f.origin === "model");
  const [correcting, setCorrecting] = useState<string | null>(null);
  // Returns null on success, else the message. A failed correction shows its message inside the editor, next to
  // the field (Outsider review), not in the panel-level banner.
  async function decide(f: AiField, outcome: "accepted" | "rejected" | "unsure" | "corrected", corrected?: object): Promise<string | null> {
    setBusy(f.field_id);
    setErr(null);
    try {
      await api.post(`cases/${caseId}/ai/fields/${f.field_id}/review`, { outcome, supersedes: f.review?.event_id ?? null, ...(corrected ? { corrected } : {}) });
      setChanging(null);
      setCorrecting(null);
      await onChanged();
      return null;
    } catch (e) {
      const conflict = e instanceof ApiError && e.code === "REVIEW_CONFLICT";
      const msg =
        e instanceof ApiError
          ? conflict
            ? "Someone else decided this value meanwhile. The list was refreshed."
            : e.code === "PII_DETECTED"
              ? "The correction looks like it contains a name, phone number or ID. Enter the clinical value only. Nothing was saved."
              : `${e.message}. Nothing was saved.`
          : "Could not save the decision. Nothing was saved.";
      if (outcome !== "corrected" || conflict) setErr(msg);
      if (conflict) await onChanged();
      return msg;
    } finally {
      setBusy(null);
    }
  }
  return (
    <Card>
      <h2 className="text-xl font-bold">AI-extracted values: your decision</h2>
      <p className="text-muted">Each value shows the exact words it came from. Accept only what the patient confirms. Only accepted values are filled in on the review step; red flags are never ticked for you.</p>
      {err && (
        <Notice tone="error" role="alert" className="mt-2">
          {err}
        </Notice>
      )}
      <ul className="mt-3 space-y-3">
        {fields.map((f) => (
          <li key={f.field_id} className={`rounded-lg border p-3 ${f.priority_review ? "border-2 border-warning" : "border-subtle"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-bold">{f.field.replace(/^(symptom|medication|red_flag):/, "").replaceAll("_", " ")}</span>
              <span>{show(f.value)}</span>
              <SourceTag kind={f.review?.outcome === "accepted" ? "ai_reviewed" : f.review?.outcome === "corrected" ? "ai_corrected" : "ai_pending"} />
              {f.agreement && <span className="text-sm text-muted">the AI read it the same way {f.agreement.replace("/", " of ")} times</span>}
            </div>
            {f.status === "disputed" || f.status === "disputed_raise" ? (
              <p className="mt-1 text-sm font-bold text-warning">
                The AI gave different answers when it read this again{f.candidates.length ? `: ${f.candidates.map((c) => (c.not_mentioned ? "not mentioned" : show(c.value))).join(" vs ")}` : ""}. Check with the patient.
              </p>
            ) : null}
            {f.evidence[0]?.quote && <p className="mt-1 text-sm">From the words: “{f.evidence[0].quote}”</p>}
            {f.review && changing !== f.field_id ? (
              <p className="mt-2 flex flex-wrap items-center gap-3 text-sm">
                <span>
                  Decision: <strong>{OUTCOME_WORDS[f.review.outcome] ?? f.review.outcome}</strong>
                  {f.review.outcome === "corrected" && f.review.corrected && <> — your entry: <strong>{correctedText(f.review.corrected)}</strong></>}
                </span>
                <button type="button" className="min-h-11 text-primary underline underline-offset-4" onClick={() => setChanging(f.field_id)}>
                  Change decision<span className="sr-only"> for {f.field}</span>
                </button>
              </p>
            ) : (
              <div className="mt-2 flex flex-wrap gap-2">
                {f.value !== null && (
                  <Button variant="secondary" disabled={busy !== null} onClick={() => decide(f, "accepted")}>
                    <Icon name="check" /> Accept
                  </Button>
                )}
                <Button variant="secondary" disabled={busy !== null} onClick={() => decide(f, "unsure")}>
                  Not sure
                </Button>
                {correctionKindOf(f.kind) && (
                  <Button variant="secondary" disabled={busy !== null} aria-expanded={correcting === f.field_id} onClick={() => setCorrecting(correcting === f.field_id ? null : f.field_id)}>
                    <Icon name="pencil" /> Edit value…<span className="sr-only"> {f.field}</span>
                  </Button>
                )}
                <Button variant="danger" disabled={busy !== null} onClick={() => decide(f, "rejected")}>
                  <Icon name="cross" /> Reject
                </Button>
              </div>
            )}
            {correcting === f.field_id && correctionKindOf(f.kind) && (
              <CorrectionEditor
                field={f}
                kind={correctionKindOf(f.kind)!}
                busy={busy === f.field_id}
                onCancel={() => setCorrecting(null)}
                onSave={(corrected) => decide(f, "corrected", corrected)}
              />
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

const CORRECTION_LABELS: Record<CorrectionKind, { value: string; hint: string }> = {
  measurement: { value: "Value the patient confirms", hint: "A number, for example 101.4. Not rounded." },
  text: { value: "What the patient says", hint: "Up to 200 characters. No names, phone numbers or ID numbers." },
  medication: { value: "Medicine name", hint: "As the patient confirms it. No names, phone numbers or ID numbers." },
  symptom: { value: "", hint: "" },
  red_flag: { value: "", hint: "" },
};

// Inline correction of one AI-extracted value. The correction is the health worker's own entry, recorded with their
// account (append-only review event); the server re-validates it and refuses identifiers. Nothing is auto-filled.
function CorrectionEditor({ field, kind, busy, onCancel, onSave }: { field: AiField; kind: CorrectionKind; busy: boolean; onCancel: () => void; onSave: (corrected: object) => Promise<string | null> }) {
  const [form, setForm] = useState<CorrectionForm>(() => initialCorrection(kind, field.value));
  const [errors, setErrors] = useState<Partial<Record<CorrectionField, string>>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const firstError = useRef<HTMLDivElement>(null);
  const id = `corr-${field.field_id}`;
  const set = (k: CorrectionField, v: string) => {
    setForm((x) => ({ ...x, [k]: v }));
    setErrors((e) => ({ ...e, [k]: undefined }));
    setServerError(null);
  };
  async function submit(ev: React.FormEvent) {
    ev.preventDefault();
    const r = buildCorrection(kind, form);
    if (!r.ok) {
      setErrors(r.errors);
      requestAnimationFrame(() => firstError.current?.querySelector<HTMLElement>("[aria-invalid=true]")?.focus());
      return;
    }
    const failed = await onSave(r.corrected);
    if (failed) {
      // Keep what was typed, mark the value box, and move focus to it: nothing was saved.
      setServerError(failed);
      if (kind !== "symptom" && kind !== "red_flag") setErrors({ value: "Not saved — see the message below." });
      requestAnimationFrame(() => firstError.current?.querySelector<HTMLElement>("input")?.focus());
    }
  }
  const L = CORRECTION_LABELS[kind];
  return (
    <form onSubmit={submit} noValidate className="mt-3 space-y-3 rounded border-2 border-dashed border-ai bg-ai-bg p-3" aria-labelledby={`${id}-title`}>
      <p id={`${id}-title`} className="font-bold">
        Correct “{field.field.replace(/^(symptom|medication|red_flag):/, "").replaceAll("_", " ")}”
      </p>
      <div ref={firstError} className="space-y-3">
        {(kind === "measurement" || kind === "text" || kind === "medication") && (
          <Field id={`${id}-value`} label={L.value} hint={L.hint} error={errors.value}>
            <input
              id={`${id}-value`}
              className={inputClass}
              inputMode={kind === "measurement" ? "decimal" : undefined}
              value={form.value}
              maxLength={kind === "measurement" ? 12 : 200}
              onChange={(e) => set("value", e.target.value)}
              aria-invalid={errors.value ? true : undefined}
              aria-describedby={`${id}-value-hint${errors.value ? ` ${id}-value-error` : ""}`}
            />
          </Field>
        )}
        {kind === "measurement" && (
          <div className="grid gap-3 sm:grid-cols-2">
            {/* A second value only means something for blood pressure (backend effective_value/_hints use value2 for bp). */}
            {field.field.split("#")[0] === "bp" && (
            <Field id={`${id}-value2`} label="Second value (optional)" hint="Only for blood pressure: the lower number." error={errors.value2}>
              <input id={`${id}-value2`} className={inputClass} inputMode="decimal" value={form.value2} maxLength={12} onChange={(e) => set("value2", e.target.value)} aria-invalid={errors.value2 ? true : undefined} aria-describedby={`${id}-value2-hint${errors.value2 ? ` ${id}-value2-error` : ""}`} />
            </Field>
            )}
            <Field id={`${id}-unit`} label="Unit (optional)" hint="For example F, C, /min, %, mmHg." error={errors.unit}>
              <input id={`${id}-unit`} className={inputClass} value={form.unit} maxLength={20} onChange={(e) => set("unit", e.target.value)} aria-invalid={errors.unit ? true : undefined} aria-describedby={`${id}-unit-hint${errors.unit ? ` ${id}-unit-error` : ""}`} />
            </Field>
          </div>
        )}
        {(kind === "symptom" || kind === "red_flag") && (
          <fieldset className={`space-y-2 ${errors.negated ? "border-l-4 border-error pl-3" : ""}`} aria-describedby={errors.negated ? `${id}-neg-error` : undefined}>
            <legend className="font-bold">What does the patient say?</legend>
            {errors.negated && (
              <p id={`${id}-neg-error`} className="font-bold text-error">
                <span className="sr-only">Error: </span>
                {errors.negated}
              </p>
            )}
            {(["present", "denied"] as const).map((v) => (
              <label key={v} className="flex min-h-11 items-center gap-2">
                <input type="radio" name={`${id}-neg`} value={v} checked={form.negated === v} onChange={() => set("negated", v)} aria-invalid={errors.negated ? true : undefined} className="size-5" />
                {v === "present" ? "Has it" : "Denies it"}
              </label>
            ))}
          </fieldset>
        )}
      </div>
      {serverError && (
        <Notice tone="error" role="alert">
          {serverError}
        </Notice>
      )}
      <p className="text-sm">The corrected value is recorded as your entry, not as an AI value. It is only shown on the review step; it never sets urgency.</p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          <Icon name="check" /> {busy ? "Saving…" : "Save correction"}
        </Button>
        <Button variant="quiet" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

const NOT_SURE = "Not sure";

function FollowUpScreen() {
  const { caseId, role, caseView, notFound, loadError } = useCase();
  const [ext, setExt] = useState<Extraction | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "running" | "unavailable" | "error">("idle");
  const [why, setWhy] = useState<string | null>(null);
  const [intake, setIntake] = useState("");
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const qHeading = useRef<HTMLHeadingElement>(null);
  const firstQuestion = useRef(true); // the page heading gets focus on arrival; questions only on navigation
  const [rerun, setRerun] = useState(false);

  const isWorker = role === "anm";
  const triage = caseView?.consent.triage ?? null;
  const aiOk = caseView?.consent.ai_assist === "granted";

  const loadLatest = useCallback(async () => {
    if (!caseId || !isWorker || !aiOk) return;
    setState("loading");
    try {
      const list = await api.get<{ extractions: { extraction_id: string; status: string }[] }>(`cases/${caseId}/ai/extractions`);
      const last = list.extractions.at(-1);
      if (last) setExt(await api.get<Extraction>(`cases/${caseId}/ai/extractions/${last.extraction_id}`));
      setState("idle");
    } catch (err) {
      setState("error");
      setWhy(err instanceof ApiError ? err.message : "Could not load follow-up questions.");
    }
  }, [caseId, isWorker, aiOk]);

  useEffect(() => {
    loadLatest();
  }, [loadLatest]);
  useEffect(() => {
    if (!caseId) return;
    const saved = readNotes(caseId).followUps;
    setAnswers(Object.fromEntries(Object.entries(saved).map(([k, v]) => [k, v.answer])));
  }, [caseId]);

  const questions = ext ? [...ext.follow_up_questions].sort((a, b) => Number(b.is_danger_sign) - Number(a.is_danger_sign)) : [];
  const q = questions[Math.min(index, Math.max(questions.length - 1, 0))];
  // A decision on an AI value reloads the extraction, and the backend may then add or remove questions. Keep the
  // question being asked on screen by following its field name, never its old position.
  const currentField = useRef<string | null>(null);
  useEffect(() => {
    if (!ext) return;
    const at = currentField.current ? questions.findIndex((x) => x.field_name === currentField.current) : -1;
    if (at >= 0 && at !== index) setIndex(at);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ext]);
  useEffect(() => {
    currentField.current = q?.field_name ?? null;
  }, [q?.field_name]);
  useEffect(() => {
    setDraft(q ? (answers[q.field_name] ?? "") : "");
    if (firstQuestion.current) firstQuestion.current = false;
    else qHeading.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, q?.field_name]);

  if (notFound) return <CaseNotFound />;

  async function run() {
    if (!caseId) return;
    setState("running");
    setWhy(null);
    try {
      const v = await api.post<Extraction>(`cases/${caseId}/ai/extractions`, { idempotency_key: crypto.randomUUID(), intake_text: intake.trim() || null, include_voice: true, include_ocr_reviewed: true });
      setExt(v);
      setIndex(0);
      setReviewing(false);
      setRerun(false);
      setState("idle");
    } catch (err) {
      if (err instanceof ApiError && err.code === "AI_NOT_CONFIGURED") {
        setState("unavailable");
        setWhy("No AI provider is configured on this server, so follow-up questions cannot be generated. Continue to the review step and ask the questions on the form.");
      } else if (err instanceof ApiError && err.code === "AI_NO_INPUT") {
        setState("error");
        setWhy("There is nothing to work from yet. Type a short description of the symptoms (English), or record voice first.");
      } else if (err instanceof ApiError && (err.code === "PII_DETECTED" || err.code === "AI_INPUT_UNSUPPORTED_LANGUAGE")) {
        setState("error");
        setWhy(`${err.message}. Nothing was sent to the AI. Remove names and ID numbers, use English, and try again.`);
      } else {
        setState("error");
        setWhy(err instanceof ApiError ? `${err.message}${err.requestId ? ` (reference ${err.requestId})` : ""}` : "Could not reach the server. Nothing was sent; try again.");
      }
    }
  }

  function save(answer: string) {
    if (!caseId || !q) return;
    const nextAnswers = { ...answers, [q.field_name]: answer };
    setAnswers(nextAnswers);
    const notes = readNotes(caseId);
    writeNotes(caseId, { ...notes, followUps: { ...notes.followUps, [q.field_name]: { answer, question: q.question_text } } });
    if (index + 1 < questions.length) setIndex(index + 1);
    else setReviewing(true);
  }

  const shell = (children: React.ReactNode) => (
    <IntakeShell step="followup" caseId={caseId} role={role} triage={triage} token={caseView?.patient_token} scenario={caseView?.scenario} intro="Questions about information that is still missing. Ask them one at a time and note the answer.">
      {loadError && (
        <Notice tone="error" role="alert">
          {loadError}
        </Notice>
      )}
      {children}
    </IntakeShell>
  );

  if (!caseView || role === null) return shell(<Spinner label="Loading case…" />);
  if (!isWorker) return shell(<Notice tone="info" title="This step is done by the health worker">The health worker asks these questions with you.</Notice>);
  if (triage !== "granted")
    return shell(
      <Notice tone="warning" title="Consent needed first">
        Triage consent is not in effect. <a href={hrefFor("consent", caseId)}>Go to consent</a>.
      </Notice>,
    );
  if (!aiOk)
    return shell(
      <Notice tone="info" title="Follow-up questions need AI assistance consent">
        <p>The patient did not agree to AI assistance, so questions are not generated. That is fine: continue to the review step, where every value is entered and checked by you.</p>
      </Notice>,
    );

  return shell(
    <div className="space-y-5">
      <Notice tone="ai" title="AI-assisted step">
        <p>
          AI extracts facts from the English text and checked transcripts; a fixed list of questions then covers what is still missing. AI never decides urgency.
          {ext?.provider_is_fake && " On this demo server the “AI” is a simple keyword matcher, not a real AI model."}
        </p>
      </Notice>

      {(state === "error" || state === "unavailable") && why && (
        <Notice tone={state === "unavailable" ? "info" : "error"} role={state === "error" ? "alert" : "status"}>
          {why}
        </Notice>
      )}

      {(!ext || rerun) && state !== "loading" && (
        <Card>
          <Field id="intake" label="Symptoms in the patient's words (English, optional)" hint="Do not type names, phone numbers or ID numbers. Identifiers are removed before AI sees the text, but removal is not perfect.">
            <textarea id="intake" rows={4} maxLength={4000} className={inputClass} value={intake} onChange={(e) => setIntake(e.target.value)} />
          </Field>
          <div className="mt-3 flex flex-wrap gap-3">
            <Button onClick={run} disabled={state === "running"}>
              {state === "running" ? <Spinner label="Working…" /> : "Find missing information"}
            </Button>
            {rerun && (
              <Button variant="quiet" onClick={() => setRerun(false)}>
                Cancel
              </Button>
            )}
          </div>
        </Card>
      )}

      {state === "loading" && <Spinner label="Loading questions…" />}

      {ext && !rerun && (
        <Button variant="quiet" onClick={() => setRerun(true)}>
          Run again with new information
        </Button>
      )}

      {ext && ext.fields.some((f) => f.origin === "model") && <AiFieldReview caseId={caseId!} ext={ext} onChanged={loadLatest} />}

      {ext && questions.length === 0 && <Notice tone="success">No follow-up questions: the required information is present. Continue to the review step.</Notice>}

      {ext && questions.length > 0 && !reviewing && q && (
        <Card>
          <p className="text-sm font-bold text-muted">
            Question {index + 1} of {questions.length}
          </p>
          {q.is_danger_sign && (
            <p className="mt-2 inline-flex items-center gap-2 rounded bg-error-bg px-3 py-1 font-bold text-error">
              <Icon name="octagon" size={18} /> Danger sign: ask this first
            </p>
          )}
          <fieldset className="mt-2">
            <legend>
              <h2 ref={qHeading} tabIndex={-1} className="text-2xl font-bold outline-none">
                {q.question_text}
              </h2>
            </legend>
            <p className="mt-1 text-sm text-muted">
              About: {q.label}. Hindi and Odia versions are not available yet (awaiting native-speaker review); translate in person.
            </p>
            {q.response_options.length > 0 ? (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {[...q.response_options, ...(q.response_options.includes("not sure") ? [] : [NOT_SURE])].map((o) => (
                  <label key={o} className={`flex min-h-11 cursor-pointer items-center gap-3 rounded border-2 px-4 py-2 ${draft === o ? "border-primary bg-primary-tint font-bold" : "border-line bg-card"}`}>
                    <input type="radio" name="answer" className="size-5" checked={draft === o} onChange={() => setDraft(o)} />
                    {o}
                  </label>
                ))}
              </div>
            ) : (
              <div className="mt-3 space-y-2">
                <input aria-label="Answer" className={inputClass} value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={200} />
                <Button variant="quiet" onClick={() => setDraft(NOT_SURE)}>
                  Not sure
                </Button>
              </div>
            )}
          </fieldset>
          <div className="mt-4 flex flex-wrap gap-3">
            {index > 0 && (
              <Button variant="secondary" onClick={() => setIndex(index - 1)}>
                <Icon name="arrowLeft" /> Previous question
              </Button>
            )}
            <Button onClick={() => save(draft.trim() || NOT_SURE)}>
              {index + 1 < questions.length ? "Save and next" : "Save and review answers"} <Icon name="arrowRight" />
            </Button>
          </div>
        </Card>
      )}

      {ext && questions.length > 0 && reviewing && (
        <Card>
          <h2 className="text-2xl font-bold">Review the answers</h2>
          <dl className="mt-3 divide-y divide-subtle">
            {questions.map((x, i) => (
              <div key={x.field_name} className="grid gap-1 py-3 sm:grid-cols-[2fr_1fr_auto] sm:items-center">
                <dt>{x.question_text}</dt>
                <dd className="font-bold">{answers[x.field_name] ?? "Not answered"}</dd>
                <dd>
                  <button
                    type="button"
                    className="min-h-11 text-primary underline underline-offset-4"
                    onClick={() => {
                      setReviewing(false);
                      setIndex(i);
                    }}
                  >
                    Change<span className="sr-only"> answer to: {x.question_text}</span>
                  </button>
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <SourceTag kind="local_note" /> These answers are not sent. On the review step each one appears next to its field, where you enter the value.
          </p>
        </Card>
      )}

    </div>,
  );
}

export default function FollowUpPage() {
  return (
    <Suspense>
      <FollowUpScreen />
    </Suspense>
  );
}
