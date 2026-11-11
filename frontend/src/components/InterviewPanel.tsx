"use client";

// Scripted interview on the Questions step (backend app/interview; docs/plans/scripted-voice-interviewer.md).
// The questions are a fixed list for the information the triage form is still missing. The health worker asks,
// records what was said, reads it back, and confirms. Nothing here is chosen by a model; nothing diagnoses or advises.
// Measurements and danger-sign screens are shown as tasks only: they are recorded on the review step, never here.
// Progress is saved on the server after every confirmed answer, so pausing or leaving the page loses nothing.
import { useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { Button, Card, Field, Notice, Spinner, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  SPEAKER_WORDS, TEXT_MAX, answerText, buildAnswer, emptyDraft, firstUnanswered, interviewErrorWords, isKnown, patientQuestions, progress, workerTasks,
  type Draft, type InterviewQuestion, type InterviewView, type Speaker,
} from "@/lib/interview";

type Transcript = { transcription_id: string; status: string; language: string; transcript_raw: string | null; created_at: string };
type Mode = "overview" | "asking" | "paused" | "review";

function explain(err: unknown): string {
  return err instanceof ApiError ? interviewErrorWords(err.code, err.status, err.message) : interviewErrorWords(undefined, undefined, "Could not reach the server.");
}

export function InterviewPanel({ caseId, canRecord }: { caseId: string; canRecord: boolean }) {
  const [view, setView] = useState<InterviewView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("overview");
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);

  const load = useCallback(async (): Promise<InterviewView | null> => {
    try {
      const v = await api.get<InterviewView>(`cases/${caseId}/interview`);
      setView(v);
      setLoadError(null);
      return v;
    } catch (err) {
      setLoadError(explain(err).replace(" Nothing was saved.", ""));
      return null;
    }
  }, [caseId]);

  useEffect(() => {
    void load();
    // Completed voice recordings of this visit, so a typed answer can cite one. Absent when voice is off (404).
    api.get<{ transcriptions: Transcript[] }>(`cases/${caseId}/voice/transcriptions`).then(
      (d) => setTranscripts(d.transcriptions.filter((t) => t.status === "completed" && t.transcript_raw)),
      () => setTranscripts([]),
    );
  }, [caseId, load]);

  const questions = view ? patientQuestions(view) : [];
  const tasks = view ? workerTasks(view) : [];
  const p = view ? progress(view) : null;
  const q: InterviewQuestion | undefined = questions[index];

  function open(i: number, list: InterviewQuestion[] = questions) {
    if (!list[i]) return;
    setIndex(i);
    setDraft(emptyDraft(list[i]));
    setError(null);
    setSaved(null);
    setMode("asking");
    requestAnimationFrame(() => heading.current?.focus());
  }

  function start() {
    if (!view) return;
    const i = firstUnanswered(view);
    open(i === -1 ? 0 : i);
  }

  async function save() {
    if (!q || !draft) return;
    const r = buildAnswer(q, draft);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`cases/${caseId}/interview/answers`, r.body);
      const v = await load();
      setSaved(`Saved: ${q.label}.`);
      const list = v ? patientQuestions(v) : questions;
      if (index + 1 < list.length) open(index + 1, list);
      else setMode("review");
    } catch (err) {
      setError(explain(err));
      if (err instanceof ApiError && err.code === "NOT_IN_SCRIPT") {
        await load();
        setMode("overview");
      }
    } finally {
      setBusy(false);
    }
  }

  function skip() {
    if (index + 1 < questions.length) open(index + 1);
    else setMode("review");
  }

  if (loadError) {
    return (
      <Card>
        <h2 className="text-xl font-bold">Interview</h2>
        <Notice tone="error" role="alert" className="mt-2">{loadError}</Notice>
        <Button variant="secondary" className="mt-3" onClick={() => void load()}><Icon name="refresh" size={16} /> Try again</Button>
      </Card>
    );
  }
  if (!view || !p) return <Card><Spinner label="Loading the interview…" /></Card>;

  const status = p.total === 0 ? "No patient questions" : p.done ? (p.unknown ? "All asked — some still not known" : "Complete") : p.known + p.unknown > 0 ? "In progress" : "Not started";

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">Interview: fixed questions for missing information</h2>
          <p className="text-sm text-muted">{view.notice}</p>
        </div>
        <span data-testid="interview-status" className={`rounded-lg border px-3 py-1 text-sm font-bold ${p.done && !p.unknown ? "border-success/40 bg-success-bg text-success" : "border-subtle"}`}>{status}</span>
      </div>

      <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-4" aria-label="Interview progress">
        <div><dt className="text-muted">Language</dt><dd className="font-bold">English (Hindi and Odia wording awaits review)</dd></div>
        <div><dt className="text-muted">Answered</dt><dd className="font-bold" data-testid="interview-known">{p.known} of {p.total}</dd></div>
        <div><dt className="text-muted">Answered “not sure”</dt><dd className="font-bold">{p.unknown}</dd></div>
        <div><dt className="text-muted">Not asked yet</dt><dd className="font-bold">{p.unanswered}</dd></div>
      </dl>
      {p.total > 0 && (
        <div className="mt-2 h-2 overflow-hidden rounded bg-surface-2" role="progressbar" aria-label="Questions with a recorded answer" aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.known + p.unknown}>
          <div className="h-full bg-primary" style={{ width: `${((p.known + p.unknown) / p.total) * 100}%` }} />
        </div>
      )}

      {saved && mode !== "asking" && <p role="status" className="mt-2 text-sm text-success">{saved}</p>}
      {view.answers_hidden && <Notice tone="info" className="mt-3">Answers are hidden for this role. Only whether a question was answered is shown.</Notice>}

      {tasks.length > 0 && (
        <div className="mt-4 rounded-lg border border-warning/50 bg-warning-bg p-3">
          <p className="font-bold">For the health worker — record these on the review step, not here</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
            {tasks.map((t) => (
              <li key={t.key}>
                {t.is_danger_sign && <strong className="text-error">Danger-sign screen: </strong>}
                {t.question_text}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs">These are never asked aloud by the interview and never completed by it. Until they are recorded they stay missing.</p>
        </div>
      )}

      {p.total === 0 && <Notice tone="info" className="mt-4">There is no patient question to ask right now. The list follows what the triage form is still missing.</Notice>}

      {/* ── overview / paused ─────────────────────────────────────────────────────────────── */}
      {p.total > 0 && (mode === "overview" || mode === "paused") && (
        <div className="mt-4 space-y-3">
          {mode === "paused" && <Notice tone="info" role="status">Paused. Every confirmed answer is already saved; nothing is lost if you leave this page.</Notice>}
          {!canRecord && <Notice tone="info">Read only: answers are recorded by the health worker who handles this visit.</Notice>}
          <div className="flex flex-wrap gap-3">
            {canRecord && (
              <Button onClick={start}>
                <Icon name="arrowRight" /> {mode === "paused" ? "Resume interview" : p.known + p.unknown > 0 ? (p.done ? "Edit answers" : "Continue interview") : "Start interview"}
              </Button>
            )}
            {p.known + p.unknown > 0 && <Button variant="secondary" onClick={() => setMode("review")}>Review answers</Button>}
          </div>
        </div>
      )}

      {/* ── one question ──────────────────────────────────────────────────────────────── */}
      {mode === "asking" && q && draft && (
        <form
          className="mt-4 space-y-4 rounded-lg border-2 border-primary/40 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          noValidate
        >
          <p className="text-sm font-bold text-muted">Question {index + 1} of {questions.length} · optional: you may skip it</p>
          <fieldset>
            <legend>
              <h3 ref={heading} tabIndex={-1} className="text-2xl font-bold outline-none">{q.question_text}</h3>
            </legend>
            <p className="mt-1 text-sm text-muted">About: {q.label}. Ask it in these words; translate in person if needed. Do not suggest an answer.</p>

            {q.kind === "choice" ? (
              <div className="mt-3 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={q.question_text}>
                {q.options.map((o) => (
                  <label key={o} className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border-2 px-4 py-2 ${draft.choice === o ? "border-primary bg-primary-tint font-bold" : "border-line bg-card"}`}>
                    <input type="radio" name="interview-choice" className="size-5" checked={draft.choice === o} onChange={() => setDraft({ ...draft, choice: o, confirmed: false })} />
                    {o}
                  </label>
                ))}
              </div>
            ) : (
              <div className="mt-3 space-y-3">
                <Field id="interview-text" label="Answer, in the words that were said" hint={`Up to ${TEXT_MAX} characters. No names, phone numbers or ID numbers.`}>
                  <textarea
                    id="interview-text"
                    rows={3}
                    maxLength={TEXT_MAX}
                    className={inputClass}
                    value={draft.text}
                    onChange={(e) => setDraft({ ...draft, text: e.target.value, confirmed: false })}
                    aria-describedby="interview-text-hint"
                  />
                </Field>
                {transcripts.length > 0 && (
                  <Field id="interview-transcript" label="Take the answer from a voice recording of this visit (optional)" hint="The recording’s text is copied in for you to check and edit. The answer then cites that recording as its source.">
                    <select
                      id="interview-transcript"
                      className={inputClass}
                      value={draft.transcript?.transcription_id ?? ""}
                      onChange={(e) => {
                        const t = transcripts.find((x) => x.transcription_id === e.target.value);
                        setDraft(t ? { ...draft, text: (t.transcript_raw ?? "").slice(0, TEXT_MAX), transcript: { transcription_id: t.transcription_id, text: t.transcript_raw ?? "" }, confirmed: false }
                          : { ...draft, transcript: null, confirmed: false });
                      }}
                    >
                      <option value="">Typed by me (no recording)</option>
                      {transcripts.map((t) => (
                        <option key={t.transcription_id} value={t.transcription_id}>
                          {new Date(t.created_at).toLocaleTimeString()} · {(t.transcript_raw ?? "").slice(0, 60)}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
              </div>
            )}
          </fieldset>

          <Field id="interview-speaker" label="Who answered?">
            <select id="interview-speaker" className={inputClass} value={draft.speaker} onChange={(e) => setDraft({ ...draft, speaker: e.target.value as Speaker, confirmed: false })}>
              {(Object.keys(SPEAKER_WORDS) as Speaker[]).map((s) => <option key={s} value={s}>{SPEAKER_WORDS[s]}</option>)}
            </select>
          </Field>

          <div className="rounded-lg border border-subtle p-3" aria-live="polite">
            <p className="text-sm text-muted">What will be recorded (read it back before confirming)</p>
            <p className="font-bold" data-testid="interview-preview">
              {q.label}: {q.kind === "choice" ? draft.choice ?? "—" : draft.text.trim() || "—"} <span className="font-normal text-muted">· said by {SPEAKER_WORDS[draft.speaker].toLowerCase()}</span>
            </p>
            {q.kind === "choice" && draft.choice === "not sure" && <p className="text-sm text-warning">“Not sure” is recorded as said. The information stays missing.</p>}
            <label className="mt-2 flex min-h-11 items-start gap-2">
              <input type="checkbox" className="mt-1 size-5" checked={draft.confirmed} onChange={(e) => setDraft({ ...draft, confirmed: e.target.checked })} />
              I read this back and it is what was said.
            </label>
          </div>

          {error && <Notice tone="error" role="alert">{error}</Notice>}

          <div className="flex flex-wrap gap-3">
            {index > 0 && <Button variant="secondary" disabled={busy} onClick={() => open(index - 1)}><Icon name="arrowLeft" /> Previous</Button>}
            <Button type="submit" disabled={busy}>
              {busy ? <Spinner label="Saving…" /> : <>{index + 1 < questions.length ? "Confirm and next" : "Confirm and review"} <Icon name="arrowRight" /></>}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={skip}>Skip this question</Button>
            <Button variant="quiet" disabled={busy} onClick={() => { setError(null); setMode("paused"); }}>Pause</Button>
          </div>
          <p className="text-xs text-muted">Skipping or pausing records nothing for this question. An answer is saved only when you confirm it.</p>
        </form>
      )}

      {/* ── review ────────────────────────────────────────────────────────────────────── */}
      {mode === "review" && (
        <div className="mt-4 space-y-3">
          <h3 className="text-lg font-bold">Answers recorded for this visit</h3>
          <dl className="divide-y divide-subtle">
            {questions.map((x, i) => (
              <div key={x.key} className="grid gap-1 py-3 sm:grid-cols-[2fr_2fr_auto] sm:items-center" data-testid={`interview-row-${x.key}`}>
                <dt>{x.question_text}</dt>
                <dd>
                  {view.answers_hidden ? (
                    <span className="text-muted">{x.answered ? "Answered (hidden for this role)" : "Not answered"}</span>
                  ) : (
                    <>
                      <strong>{answerText(x.answer)}</strong>
                      {x.answer && (
                        <span className="block text-xs text-muted">
                          {SPEAKER_WORDS[x.answer.speaker]} · {x.answer.source === "transcript" ? "from a voice recording" : "typed"} · confirmed by the health worker
                          {!isKnown(x) && " · still missing"}
                        </span>
                      )}
                    </>
                  )}
                </dd>
                <dd>
                  {canRecord && (
                    <button type="button" className="min-h-11 text-primary underline underline-offset-4" onClick={() => open(i)}>
                      {x.answered ? "Change" : "Answer"}<span className="sr-only">: {x.question_text}</span>
                    </button>
                  )}
                </dd>
              </div>
            ))}
          </dl>
          {p.done && p.unknown === 0 ? (
            <Notice tone="success" role="status" title="Interview complete">
              Every patient question has a confirmed answer. The answers appear in the medical officer’s summary as a table; a summary made earlier is marked out of date.
            </Notice>
          ) : (
            <Notice tone="warning" role="status" title="Still missing">
              {questions.filter((x) => !isKnown(x)).map((x) => x.label).join(", ")}. Unanswered and “not sure” items stay listed as missing; they are never treated as normal.
            </Notice>
          )}
          <div className="flex flex-wrap gap-3">
            {canRecord && !p.done && <Button onClick={start}>Continue interview</Button>}
            <Button variant="secondary" onClick={() => setMode("overview")}>Close review</Button>
          </div>
        </div>
      )}
    </Card>
  );
}
