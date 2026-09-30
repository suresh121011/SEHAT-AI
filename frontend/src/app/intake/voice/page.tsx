"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type FormEvent, type ReactNode, Suspense, useCallback, useEffect, useState } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { ApiError, api } from "@/lib/api";
import { useMatchingVoice } from "@/lib/useMatchingVoice";

type Language = "en" | "hi" | "or";
type Engine = "local" | "cloud";
type State = "not_provided" | "granted" | "declined" | "withdrawn";
type CaseView = { case_id: string; patient_token: string; is_creator: boolean; consent: { triage: State; ai_assist: State; voice_cloud: State } };
type EngineCaps = { enabled: boolean; ready: boolean; label: string; model: string; languages: Record<Language, boolean> };
type Caps = {
  voice_enabled: boolean;
  engines: Record<Engine, EngineCaps>;
  tts: { enabled: boolean; ready: boolean };
  verification: Record<Engine, Record<Language, string>>;
  max_seconds: number;
};
type Resolution = { event_id: string; outcome: Outcome; field: string; values: Record<string, number | boolean> | null; actor_role: string; at: string };
type Outcome = "confirmed" | "corrected" | "rejected" | "unsure";
type Candidate = {
  candidate_id: string;
  field: string;
  char_start: number;
  char_end: number;
  heard_text: string;
  flags: string[];
  display: string;
  readback_text: string;
  can_confirm: boolean;
  resolution: Resolution | null;
};
type Transcription = {
  transcription_id: string;
  status: "completed" | "no_speech" | "empty_transcript" | "failed" | "pending";
  failure_code: string | null;
  language: Language;
  engine: Engine;
  processing: string;
  model_id: string | null;
  transcript_raw: string | null;
  readback_template_status: string;
  candidates: Candidate[];
  created_at: string;
};
type Source = { type: "voice_transcript" | "voice_manual_correction"; resolved_by_role: string; transcript_chars: [number, number] };
type Prefill = {
  vitals: Record<string, number>;
  fields: Record<string, number | boolean>;
  values: Record<string, { values: Record<string, number | boolean>; sources: Source[] }>;
  unresolved: { candidate_id: string; field: string; state: "undecided" | "unsure" }[];
  conflicts: Record<string, { values: Record<string, number | boolean> }[]>;
  note: string;
};

const LANGUAGES: { code: Language; label: string; speech: string }[] = [
  { code: "en", label: "English", speech: "en" },
  { code: "hi", label: "हिन्दी", speech: "hi" },
  { code: "or", label: "ଓଡ଼ିଆ", speech: "or" },
];

const FLAG_TEXT: Record<string, string> = {
  unit_inferred: "Unit was not said — guessed from the number",
  unit_unknown: "Unit unclear — please correct",
  out_of_domain_range: "Outside the range the system accepts — please correct",
  non_integer: "Expected a whole number",
  decimal_ambiguity: "Check the decimal point (e.g. 102 vs 100.2)",
  negation: "Said with 'no/not' — check the meaning",
  uncertainty: "Said with 'maybe/about'",
  temporal_reference: "May refer to an earlier time (e.g. yesterday)",
  multiple_values: "More than one value heard for this",
  oxygen_context: "Oxygen support mentioned — record it separately",
  bp_order_invalid: "Blood pressure numbers look reversed",
  age_unit_months: "Age in months — enter years if needed",
  needs_assignment: "Number without a label — choose what it measures or leave blank",
  number_modifier_unparsed: "A word like 'saadhe/sawa/half' changes this number — enter the value yourself",
  number_sequence_ambiguous: "Numbers spoken in parts (e.g. 'one twenty') — enter the value yourself",
  unit_unclear: "The unit word was not clear (°C or °F?) — enter the value and unit yourself",
  number_words: "Heard as number words — check the value",
};

const OUTCOME_TEXT: Record<Outcome, string> = {
  confirmed: "Confirmed",
  corrected: "Corrected by health worker",
  rejected: "Rejected — wrong or not said (left blank)",
  unsure: "Not sure — left blank for checking",
};

const FIELDS = [
  ["temp", "Temperature"],
  ["spo2", "Oxygen (SpO₂ %)"],
  ["pulse", "Pulse (/min)"],
  ["resp_rate", "Breathing rate (/min)"],
  ["bp", "Blood pressure"],
  ["age", "Age (years)"],
  ["pregnancy", "Pregnant"],
] as const;

const VITAL_LABEL: Record<string, string> = {
  temp_c: "Temperature °C (shown to 2 decimals; the exact value is used)",
  spo2: "SpO₂ %",
  pulse: "Pulse /min",
  resp_rate: "Breathing /min",
  sbp: "Systolic BP",
  dbp: "Diastolic BP",
  age_years: "Age (years)",
  pregnant: "Pregnant",
};

function Highlighted({ text, candidates }: { text: string; candidates: Candidate[] }) {
  const parts: ReactNode[] = [];
  let pos = 0;
  for (const c of [...candidates].sort((a, b) => a.char_start - b.char_start)) {
    if (c.char_start < pos) continue;
    parts.push(text.slice(pos, c.char_start));
    parts.push(
      <mark key={c.candidate_id} className="rounded bg-yellow-200 px-0.5 dark:bg-yellow-700">
        {text.slice(c.char_start, c.char_end)}
      </mark>,
    );
    pos = c.char_end;
  }
  parts.push(text.slice(pos));
  return <>{parts}</>;
}

function CorrectionForm({ candidate, onSubmit, busy }: { candidate: Candidate; busy: boolean; onSubmit: (body: Record<string, unknown>) => void }) {
  // No default for unlabelled numbers: the reviewer must choose what was measured.
  const initialField = candidate.field === "unassigned" || candidate.field === "symptom_duration" ? "" : candidate.field;
  const [field, setField] = useState<string>(initialField);
  const [value, setValue] = useState("");
  const [value2, setValue2] = useState("");
  const [unit, setUnit] = useState<"" | "c" | "f">("");
  const [pregnant, setPregnant] = useState<"1" | "0">("1");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!field || (field === "temp" && !unit)) return;
    const body: Record<string, unknown> = { outcome: "corrected", field };
    if (field === "pregnancy") body.value = Number(pregnant);
    else body.value = value === "" ? null : Number(value);
    if (field === "bp") body.value2 = value2 === "" ? null : Number(value2);
    if (field === "temp") body.unit = unit;
    if (field === "age") body.unit = "years";
    onSubmit(body);
  }
  return (
    <form onSubmit={submit} className="mt-2 flex flex-wrap items-end gap-2 text-sm">
      <label className="flex flex-col">
        Measures
        <select required value={field} onChange={(e) => setField(e.target.value)} className="rounded border px-2 py-1 dark:bg-black">
          <option value="" disabled>
            Choose…
          </option>
          {FIELDS.map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {field === "pregnancy" ? (
        <label className="flex flex-col">
          Value
          <select value={pregnant} onChange={(e) => setPregnant(e.target.value as "1" | "0")} className="rounded border px-2 py-1 dark:bg-black">
            <option value="1">Pregnant</option>
            <option value="0">Not pregnant</option>
          </select>
        </label>
      ) : (
        <label className="flex flex-col">
          {field === "bp" ? "Systolic" : "Correct value"}
          <input inputMode="decimal" required value={value} onChange={(e) => setValue(e.target.value)} className="w-24 rounded border px-2 py-1 dark:bg-black" />
        </label>
      )}
      {field === "bp" && (
        <label className="flex flex-col">
          Diastolic
          <input inputMode="numeric" required value={value2} onChange={(e) => setValue2(e.target.value)} className="w-24 rounded border px-2 py-1 dark:bg-black" />
        </label>
      )}
      {field === "temp" && (
        <label className="flex flex-col">
          Unit
          <select required value={unit} onChange={(e) => setUnit(e.target.value as "" | "c" | "f")} className="rounded border px-2 py-1 dark:bg-black">
            <option value="" disabled>
              Choose…
            </option>
            <option value="f">°F</option>
            <option value="c">°C</option>
          </select>
        </label>
      )}
      <button type="submit" disabled={busy} className="rounded bg-blue-600 px-3 py-1 font-medium text-white disabled:opacity-60">
        Save correction
      </button>
    </form>
  );
}

function CandidateCard({ c, language, reviewer, ttsReady, busy, onDecide, caseId }: { c: Candidate; language: Language; reviewer: boolean; ttsReady: boolean; busy: boolean; caseId: string; onDecide: (id: string, body: Record<string, unknown>) => void }) {
  const [correcting, setCorrecting] = useState(false);
  const [speech, setSpeech] = useState<string | null>(null);
  const { voice } = useMatchingVoice(LANGUAGES.find((l) => l.code === language)!.speech);

  async function listen() {
    setSpeech(null);
    if (ttsReady) {
      try {
        const blob = await api.postForBlob(`cases/${caseId}/voice/candidates/${c.candidate_id}/tts`);
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => {
          URL.revokeObjectURL(url);
          setSpeech("Spoken read-back finished (Sarvam voice).");
        };
        audio.onerror = () => setSpeech("Spoken read-back could not be played. Read the text aloud.");
        setSpeech("Playing…");
        await audio.play();
        return;
      } catch {
        // fall through to the device voice, and say so
      }
    }
    if (voice) {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(c.readback_text);
      u.voice = voice;
      u.lang = voice.lang;
      u.onend = () => setSpeech("Spoken read-back finished (this device's voice).");
      u.onerror = () => setSpeech("Spoken read-back could not be played. Read the text aloud.");
      setSpeech("Playing…");
      window.speechSynthesis.speak(u);
    } else {
      setSpeech("Spoken read-back is unavailable for this language on this device. Read the text aloud to the patient.");
    }
  }

  return (
    <li className="space-y-2 rounded border border-black/10 p-3 dark:border-white/15">
      <p lang={language} className="text-base font-medium">
        {c.readback_text}
      </p>
      <p className="text-xs opacity-70">
        Heard: “<span lang={language}>{c.heard_text}</span>”
      </p>
      {c.flags.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {c.flags.map((f) => (
            <li key={f} className="rounded border border-orange-500 px-2 py-0.5 text-xs">
              ⚠ {FLAG_TEXT[f] ?? f}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={listen} className="rounded border border-black/20 px-3 py-1 text-sm dark:border-white/20">
          🔊 Listen
        </button>
        {speech && <span className="text-xs opacity-80" aria-live="polite">{speech}</span>}
      </div>
      {c.resolution ? (
        <p className="text-sm">
          <strong>{OUTCOME_TEXT[c.resolution.outcome]}</strong>
          {c.resolution.values && ` → ${Object.entries(c.resolution.values).map(([k, v]) => `${VITAL_LABEL[k] ?? k}: ${typeof v === "number" && k === "temp_c" ? v.toFixed(2) : String(v)}`).join(", ")}`}
          <span className="opacity-60"> · by {c.resolution.actor_role}</span>
        </p>
      ) : reviewer ? null : (
        <p className="text-sm opacity-80">A health worker will check this value with you.</p>
      )}
      {reviewer && (
        <div className="space-y-1">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Read-back decision">
            <button type="button" disabled={busy || !c.can_confirm} onClick={() => onDecide(c.candidate_id, { outcome: "confirmed", supersedes: c.resolution?.event_id ?? null })} className="rounded bg-green-700 px-3 py-1 text-sm font-medium text-white disabled:opacity-50">
              ✓ Yes, that&apos;s right
            </button>
            <button type="button" disabled={busy} onClick={() => setCorrecting((v) => !v)} className="rounded border border-blue-600 px-3 py-1 text-sm">
              ✎ Change value
            </button>
            <button type="button" disabled={busy} onClick={() => onDecide(c.candidate_id, { outcome: "unsure", supersedes: c.resolution?.event_id ?? null })} className="rounded border border-black/20 px-3 py-1 text-sm dark:border-white/20">
              ? Not sure
            </button>
            <button type="button" disabled={busy} onClick={() => onDecide(c.candidate_id, { outcome: "rejected", supersedes: c.resolution?.event_id ?? null })} className="rounded border border-red-600 px-3 py-1 text-sm">
              ✗ Wrong / not said
            </button>
          </div>
          <p className="text-xs opacity-70">“Not sure” and “Wrong / not said” both leave the value blank, so it is checked by a person. Nothing is filled in without a decision.</p>
          {!c.can_confirm && <p className="text-xs">This value cannot be accepted as heard (see the warnings) — enter it with “Change value”, or leave it blank.</p>}
          {correcting && <CorrectionForm candidate={c} busy={busy} onSubmit={(body) => onDecide(c.candidate_id, { ...body, supersedes: c.resolution?.event_id ?? null })} />}
        </div>
      )}
    </li>
  );
}

function VoiceScreen() {
  const caseId = useSearchParams().get("case");
  const [role, setRole] = useState<string | null>(null);
  const [caseView, setCaseView] = useState<CaseView | null>(null);
  const [caps, setCaps] = useState<Caps | null>(null);
  const [language, setLanguage] = useState<Language>("en");
  const [engine, setEngine] = useState<Engine | null>(null);
  const [items, setItems] = useState<Transcription[]>([]);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<{ wav: Blob; key: string } | null>(null);
  const [notFound, setNotFound] = useState(false);

  const reviewer = role === "anm" || role === "medical_officer";

  const refresh = useCallback(async () => {
    if (!caseId) return;
    try {
      const cv = await api.get<CaseView>(`cases/${caseId}`);
      setCaseView(cv);
      if (cv.consent.triage === "granted") {
        setItems((await api.get<{ transcriptions: Transcription[] }>(`cases/${caseId}/voice/transcriptions`)).transcriptions.reverse());
        if (reviewer) setPrefill(await api.get<Prefill>(`cases/${caseId}/voice/prefill`));
      }
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 400) && err.code !== "FEATURE_DISABLED") setNotFound(true);
    }
  }, [caseId, reviewer]);

  useEffect(() => {
    api
      .get<{ role: string }>("auth/me")
      .then((me) => setRole(me.role))
      .catch(() => setMessage("Could not check your session. Please log in again."));
    api
      .get<Caps>("voice/capabilities")
      .then(setCaps)
      .catch(() => setMessage("Could not load voice settings from the server. You can type the symptoms instead."));
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const cloudConsent = caseView?.consent.voice_cloud === "granted";
  function engineStatus(e: Engine): { usable: boolean; why: string } {
    const ec = caps?.engines[e];
    if (!caps?.voice_enabled) return { usable: false, why: "voice input is off on this server" };
    if (!ec?.enabled) return { usable: false, why: "not enabled on this server" };
    if (!ec.languages[language]) return { usable: false, why: "does not support this language" };
    if (!ec.ready) return { usable: false, why: e === "local" ? "speech model not installed" : "not configured" };
    if (e === "cloud" && !cloudConsent) return { usable: false, why: "patient has not agreed to online speech processing" };
    return { usable: true, why: caps.verification[e][language] === "tested_real" ? "checked on one synthetic clip only" : "not yet checked on real speech in this language" };
  }
  const usable = (["local", "cloud"] as Engine[]).filter((e) => engineStatus(e).usable);
  const selected = engine && usable.includes(engine) ? engine : usable[0] ?? null;

  function explain(err: unknown): string {
    if (!(err instanceof ApiError)) return "Something went wrong. Your recording was not saved; you can retry.";
    const reason = (err.details?.reason as string | undefined) ?? "";
    switch (err.code) {
      case "CONSENT_REQUIRED":
        return "Consent for this is not in effect. Nothing was sent.";
      case "CONSENT_WITHDRAWN":
        return "Consent changed while processing. The result was discarded.";
      case "CLOUD_STT_UNAVAILABLE":
        return `Online speech-to-text failed (${reason}). No transcript was produced. You can retry${caps?.engines.local.languages[language] ? ", choose on-device," : ""} or type instead.`;
      case "LOCAL_ASR_UNAVAILABLE":
        return `On-device speech-to-text is unavailable (${reason}). Nothing was sent online.`;
      case "LANGUAGE_UNSUPPORTED":
        return "This engine does not support the selected language.";
      case "AUDIO_INVALID":
      case "AUDIO_TOO_LARGE":
        return "The recording could not be used (format or length). Please record again.";
      case "IN_PROGRESS":
        return "This recording is still being processed.";
      case "STALE_DECISION":
        refresh();
        return "Someone else decided this value in the meantime. The screen has been refreshed — please review again.";
      case "CORRECTION_REQUIRED":
        return "This value could not be read reliably — please correct it or leave it blank.";
      case "CORRECTION_INVALID":
        return `That corrected value is not valid (${reason}).`;
      default:
        return err.message;
    }
  }

  async function upload(wav: Blob, key: string) {
    if (!caseId || !selected) return;
    setBusy(true);
    setMessage(null);
    setPending({ wav, key });
    try {
      await api.postAudio(`cases/${caseId}/voice/transcriptions?language=${language}&engine=${selected}&idempotency_key=${key}`, wav);
      setPending(null);
      await refresh();
    } catch (err) {
      setMessage(explain(err));
      if (err instanceof ApiError && err.status < 500 && err.code !== "IN_PROGRESS") setPending(null);
      // A finished failure is stored under this key; a retry must be a new attempt (new key).
      else if (!(err instanceof ApiError && err.code === "IN_PROGRESS")) setPending({ wav, key: crypto.randomUUID() });
    } finally {
      setBusy(false);
    }
  }

  async function decide(candidateId: string, body: Record<string, unknown>) {
    if (!caseId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/voice/candidates/${candidateId}/readback`, body);
      await refresh();
    } catch (err) {
      setMessage(explain(err));
    } finally {
      setBusy(false);
    }
  }

  if (!caseId || notFound) {
    return (
      <section className="mx-auto max-w-xl space-y-4">
        <DemoBanner />
        <p role="alert">Case not found.</p>
        <Link href="/intake" className="text-blue-600 underline">
          Start a new case
        </Link>
      </section>
    );
  }

  const triageOk = caseView?.consent.triage === "granted";
  const canRecord = !!caseView?.is_creator && (role === "patient" || role === "anm");

  return (
    <section className="mx-auto max-w-2xl space-y-5">
      <DemoBanner />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Describe symptoms by voice</h1>
        <p className="w-full text-sm opacity-80">Measurements that are heard (temperature, pulse, oxygen…) are picked out for checking. Symptoms stay in the transcript for the reviewer to read.</p>
        <span className="font-mono text-xs opacity-70">Case {caseView?.patient_token ?? "…"}</span>
      </div>
      <p className="rounded border border-blue-600/40 px-3 py-2 text-sm">
        Voice only <strong>pre-fills</strong> values after a health worker checks each one. It does not decide urgency; the health worker still records triage. A value confirmed here can still be wrong if the reading itself was wrong.
      </p>

      {caps && !caps.voice_enabled && <p role="note">Voice input is turned off on this server. Please type the symptoms instead.</p>}

      {caseView && !triageOk && (
        <p role="alert">
          Triage consent is not in effect for this case.{" "}
          <Link href={`/intake/consent?case=${caseId}`} className="text-blue-600 underline">
            Go to consent
          </Link>
        </p>
      )}

      {triageOk && caps?.voice_enabled && (
        <>
          <fieldset className="flex flex-wrap gap-2" aria-label="Spoken language">
            {LANGUAGES.map((l) => (
              <button key={l.code} type="button" onClick={() => setLanguage(l.code)} aria-pressed={language === l.code} className={`rounded border px-3 py-1 ${language === l.code ? "border-blue-600 bg-blue-50 dark:bg-blue-950" : "border-black/20 dark:border-white/20"}`}>
                {l.label}
              </button>
            ))}
          </fieldset>

          <fieldset className="space-y-1 text-sm">
            <legend className="font-medium">Speech-to-text</legend>
            {(["local", "cloud"] as Engine[]).map((e) => {
              const st = engineStatus(e);
              return (
                <label key={e} className={`flex items-start gap-2 ${st.usable ? "" : "opacity-60"}`}>
                  <input type="radio" name="engine" disabled={!st.usable} checked={selected === e} onChange={() => setEngine(e)} />
                  <span>
                    {caps.engines[e].label} <span className="opacity-70">— {st.why}</span>
                  </span>
                </label>
              );
            })}
            {!selected && <p>No speech-to-text option is available for this language right now. Please type the symptoms instead.</p>}
          </fieldset>

          {canRecord && (
            <VoiceRecorder
              maxSeconds={caps.max_seconds}
              disabled={busy || !selected}
              onRecorded={(wav) => upload(wav, crypto.randomUUID())}
              samples={[
                { label: "Sample: English fever (synthetic)", url: "/voice-samples/en_fever_102.wav" },
                { label: "Sample: Hindi fever (synthetic)", url: "/voice-samples/hi_fever_102.wav" },
                { label: "Sample: Hindi 'saadhe 39' (synthetic)", url: "/voice-samples/hi_fever_saadhe.wav" },
              ]}
            />
          )}
          {pending && !busy && (
            <button type="button" onClick={() => upload(pending.wav, pending.key)} className="rounded border border-black/20 px-3 py-1 text-sm dark:border-white/20">
              Retry the same recording
            </button>
          )}
          {busy && <p aria-live="polite">Processing… (on-device models can take a while on the first run)</p>}
        </>
      )}

      {message && (
        <p role="alert" className="text-sm text-red-600">
          {message}
        </p>
      )}

      {items.map((t) => (
        <article key={t.transcription_id} className="space-y-3 rounded border border-black/10 p-4 dark:border-white/15">
          <p className="text-sm">
            <strong>Transcribed by: {t.engine === "local" ? "On this device (local model)" : "Internet — Sarvam AI"}</strong>
            <span className="opacity-60"> · {t.model_id ?? "—"} · {new Date(t.created_at).toLocaleTimeString()}</span>
            {caps && (
              <span className="ml-2 rounded border border-black/20 px-1.5 text-xs dark:border-white/20">
                {caps.verification[t.engine][t.language] === "tested_real" ? "checked on a synthetic clip" : caps.verification[t.engine][t.language] === "tested_mock" ? "not checked on real speech" : "unverified"}
              </span>
            )}
          </p>
          {t.status === "no_speech" && <p>No speech was detected in this recording. Nothing was transcribed.</p>}
          {t.status === "empty_transcript" && <p>The speech could not be turned into text. Please try again or type instead.</p>}
          {t.status === "failed" && <p>This recording failed ({t.failure_code}). No transcript was kept.</p>}
          {t.transcript_raw && (
            <div>
              <h2 className="text-sm font-semibold">Raw transcript (not checked)</h2>
              <p lang={t.language} className="whitespace-pre-wrap rounded bg-black/5 p-2 dark:bg-white/10">
                <Highlighted text={t.transcript_raw} candidates={t.candidates} />
              </p>
            </div>
          )}
          {t.readback_template_status !== "project_draft" && t.candidates.length > 0 && (
            <p role="note" className="rounded border border-orange-500 px-3 py-1 text-xs">
              Draft translation of the read-back wording — not reviewed by a native speaker. Explain in person if unclear.
            </p>
          )}
          {t.candidates.length > 0 && (
            <ul className="space-y-2">
              {t.candidates.map((c) => (
                <CandidateCard key={c.candidate_id} c={c} language={t.language} reviewer={reviewer} ttsReady={!!caps?.tts.ready && cloudConsent} busy={busy} caseId={caseId} onDecide={decide} />
              ))}
            </ul>
          )}
          {t.status === "completed" && t.candidates.length === 0 && <p className="text-sm opacity-80">No measurements were recognised. Speak in the language selected above — English speech with Hindi/Odia selected is written phonetically and its numbers are not read. Some spoken number forms (e.g. Odia 21–99 as words) are not read yet — enter them in the triage form.</p>}
        </article>
      ))}

      {reviewer && prefill && (
        <aside className="space-y-2 rounded border border-green-700/50 p-4">
          <h2 className="font-semibold">Values the health worker confirmed</h2>
          {Object.keys(prefill.vitals).length + Object.keys(prefill.fields).length === 0 ? (
            <p className="text-sm opacity-80">None yet.</p>
          ) : (
            <ul className="text-sm">
              {Object.entries(prefill.values).flatMap(([field, entry]) =>
                Object.entries(entry.values).map(([k, v]) => (
                  <li key={k}>
                    {VITAL_LABEL[k] ?? k}: <strong>{k === "temp_c" && typeof v === "number" ? v.toFixed(2) : String(v)}</strong>{" "}
                    <span className="text-xs opacity-70">
                      ({entry.sources.map((s) => `${s.type === "voice_manual_correction" ? "entered by" : "heard, confirmed by"} ${s.resolved_by_role}`).join("; ")}) · {field}
                    </span>
                  </li>
                )),
              )}
            </ul>
          )}
          {prefill.unresolved.length > 0 && (
            <p role="alert" className="text-sm">
              {prefill.unresolved.length} heard value(s) not decided yet ({prefill.unresolved.map((u) => `${u.field}${u.state === "unsure" ? " — not sure" : ""}`).join(", ")}). They are not pre-filled.
            </p>
          )}
          {Object.keys(prefill.conflicts).length > 0 && (
            <p role="alert" className="text-sm">
              Different values were confirmed for: {Object.keys(prefill.conflicts).join(", ")}. Nothing is pre-filled for these — check with the patient.
            </p>
          )}
          <p className="text-xs opacity-70">{prefill.note}</p>
        </aside>
      )}
    </section>
  );
}

export default function VoicePage() {
  return (
    <Suspense>
      <VoiceScreen />
    </Suspense>
  );
}
