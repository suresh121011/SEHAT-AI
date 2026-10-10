"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type FormEvent, type ReactNode, Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { roleWords } from "@/components/Provenance";
import { Notice, inputClass, buttonClass, Card } from "@/components/ui";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { ApiError, api } from "@/lib/api";
import { useMatchingVoice } from "@/lib/useMatchingVoice";

type Language = "en" | "hi" | "or";
type Engine = "local" | "cloud";
type State = "not_provided" | "granted" | "declined" | "withdrawn";
type CaseView = { case_id: string; patient_token: string; is_creator: boolean; is_handler?: boolean; consent: { triage: State; ai_assist: State; voice_cloud: State } };
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
  oxygen_context: "Oxygen support mentioned — enter the value yourself and record the oxygen support in triage",
  bp_order_invalid: "Blood pressure numbers look reversed",
  age_unit_months: "Age in months — enter years if needed",
  needs_assignment: "Number without a label — choose what it measures or leave blank",
  number_modifier_unparsed: "A word like 'saadhe/sawa/half' changes this number — enter the value yourself",
  number_sequence_ambiguous: "Numbers spoken in parts (e.g. 'one twenty') — enter the value yourself",
  unit_unclear: "The unit word was not clear (°C or °F?) — enter the value and unit yourself",
  number_words: "Heard as number words — check the value",
  number_word_homograph: "This word can also mean 'times' or 'a' (e.g. 'once', 'a little') — enter the value yourself",
  context_unclear: "Other words around this value (e.g. 'below', 'to', 'in 30 seconds', someone else's age) — enter the value yourself",
  bp_shorthand_possible: "Looks like shorthand (e.g. '13 by 9' for 130/90) — enter the full blood pressure yourself",
};

// Plain-language reasons for a failed online (Sarvam) transcription; the code is still shown for support.
const CLOUD_REASON_TEXT: Record<string, string> = {
  cloud_timeout: "The online speech service did not answer in time (it may be unreachable from this network).",
  cloud_unreachable: "The online speech service could not be reached from this network.",
  cloud_rate_limited: "The online speech service is busy (too many requests). Wait a moment, then retry.",
  cloud_auth_failed: "The online speech service rejected this server's credentials.",
  cloud_not_configured: "Online speech is not configured on this server.",
  cloud_unavailable: "The online speech service reported an error.",
  cloud_bad_response: "The online speech service sent a reply that could not be read.",
  cloud_rejected: "The online speech service refused this recording.",
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
      <mark key={c.candidate_id} className="rounded bg-warning-bg px-0.5">
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
  const [pregnant, setPregnant] = useState<"" | "1" | "0">("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!field || (field === "temp" && !unit) || (field === "pregnancy" && !pregnant)) return;
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
        <select required value={field} onChange={(e) => setField(e.target.value)} className={inputClass}>
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
          <select required value={pregnant} onChange={(e) => setPregnant(e.target.value as "1" | "0")} className={inputClass}>
            <option value="" disabled>
              Choose…
            </option>
            <option value="1">Pregnant</option>
            <option value="0">Not pregnant</option>
          </select>
        </label>
      ) : (
        <label className="flex flex-col">
          {field === "bp" ? "Systolic" : "Correct value"}
          <input inputMode="decimal" required value={value} onChange={(e) => setValue(e.target.value)} className={`w-24 ${inputClass}`} />
        </label>
      )}
      {field === "bp" && (
        <label className="flex flex-col">
          Diastolic
          <input inputMode="numeric" required value={value2} onChange={(e) => setValue2(e.target.value)} className={`w-24 ${inputClass}`} />
        </label>
      )}
      {field === "temp" && (
        <label className="flex flex-col">
          Unit
          <select required value={unit} onChange={(e) => setUnit(e.target.value as "" | "c" | "f")} className={inputClass}>
            <option value="" disabled>
              Choose…
            </option>
            <option value="f">°F</option>
            <option value="c">°C</option>
          </select>
        </label>
      )}
      <button type="submit" disabled={busy} className={buttonClass("primary")}>
        Save correction
      </button>
    </form>
  );
}

function CandidateCard({ c, language, reviewer, ttsReady, busy, onDecide, caseId }: { c: Candidate; language: Language; reviewer: boolean; ttsReady: boolean; busy: boolean; caseId: string; onDecide: (id: string, body: Record<string, unknown>) => void }) {
  const [correcting, setCorrecting] = useState(false);
  const [speech, setSpeech] = useState<string | null>(null);
  const playing = speech === "Playing…"; // no decision while the read-back is still being spoken
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
        audio.onerror = () => {
          URL.revokeObjectURL(url);
          setSpeech("Spoken read-back could not be played. Read the text aloud.");
        };
        setSpeech("Playing…");
        await audio.play();
        return;
      } catch {
        // The online voice failed: fall through to the device voice, and say so in its status line.
        setSpeech("Online voice unavailable — trying this device's voice.");
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
    <Card as="li" className="space-y-2 p-3">
      <p lang={language} className="text-base font-medium">
        {c.readback_text}
      </p>
      <p className="text-xs text-muted">
        Heard: “<span lang={language}>{c.heard_text}</span>”
      </p>
      {c.flags.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {c.flags.map((f) => (
            <li key={f} className="rounded-lg border border-warning px-2 py-0.5 text-xs">
              <Icon name="alert" size={14} /> {FLAG_TEXT[f] ?? f}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={listen} className={buttonClass("secondary", "text-sm")}>
          <Icon name="speaker" /> {ttsReady ? "Listen (online voice — sends this text to Sarvam AI)" : "Listen (this device's voice)"}
        </button>
        {speech && <span className="text-xs text-muted" aria-live="polite">{speech}</span>}
      </div>
      {c.resolution ? (
        <p className="text-sm">
          <strong>{OUTCOME_TEXT[c.resolution.outcome]}</strong>
          {c.resolution.values && ` → ${Object.entries(c.resolution.values).map(([k, v]) => `${VITAL_LABEL[k] ?? k}: ${typeof v === "number" && k === "temp_c" ? v.toFixed(2) : String(v)}`).join(", ")}`}
          <span className="text-muted"> · by {roleWords(c.resolution.actor_role)}</span>
        </p>
      ) : reviewer ? null : (
        <p className="text-sm text-muted">A health worker will check this value with you.</p>
      )}
      {reviewer && (
        <div className="space-y-1">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Read-back decision">
            <button type="button" disabled={busy || playing || !c.can_confirm} onClick={() => onDecide(c.candidate_id, { outcome: "confirmed", supersedes: c.resolution?.event_id ?? null })} className={buttonClass("success", "text-sm")}>
              <Icon name="check" /> Yes, that&apos;s right
            </button>
            <button type="button" disabled={busy || playing} onClick={() => setCorrecting((v) => !v)} className={buttonClass("secondary", "text-sm")}>
              <Icon name="pencil" /> Change value
            </button>
            <button type="button" disabled={busy || playing} onClick={() => onDecide(c.candidate_id, { outcome: "unsure", supersedes: c.resolution?.event_id ?? null })} className={buttonClass("secondary", "text-sm")}>
              Not sure
            </button>
            <button type="button" disabled={busy || playing} onClick={() => onDecide(c.candidate_id, { outcome: "rejected", supersedes: c.resolution?.event_id ?? null })} className={buttonClass("danger", "text-sm")}>
              <Icon name="cross" /> Wrong / not said
            </button>
          </div>
          <p className="text-xs text-muted">“Not sure” and “Wrong / not said” both leave the value blank, so it is checked by a person. Nothing is filled in without a decision.</p>
          {!c.can_confirm && <p className="text-xs">This value cannot be accepted as heard (see the warnings) — enter it with “Change value”, or leave it blank.</p>}
          {correcting && <CorrectionForm candidate={c} busy={busy} onSubmit={(body) => onDecide(c.candidate_id, { ...body, supersedes: c.resolution?.event_id ?? null })} />}
        </div>
      )}
    </Card>
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
  // Cancels an upload still in flight when the page is left. This stops the browser sending the rest;
  // bytes the server (or, for the online engine, Sarvam) already received cannot be recalled.
  const uploadAbort = useRef<AbortController | null>(null);
  useEffect(() => () => uploadAbort.current?.abort(), []);
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
      } else {
        setItems([]); // consent withdrawn: stop showing transcripts and values
        setPrefill(null);
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
    return { usable: true, why: caps.verification[e][language] === "tested_real" ? "tried on a few test clips only" : "not yet checked on real speech in this language" };
  }
  const usable = (["local", "cloud"] as Engine[]).filter((e) => engineStatus(e).usable);
  // Sending audio to Sarvam must be an explicit choice: only the on-device engine is ever the default.
  const selected = engine && usable.includes(engine) ? engine : usable.includes("local") ? "local" : null;

  function explain(err: unknown): string {
    if (!(err instanceof ApiError)) return "Something went wrong. Your recording was not saved; you can retry.";
    const reason = (err.details?.reason as string | undefined) ?? "";
    switch (err.code) {
      case "CONSENT_REQUIRED":
        return "Consent for this is not in effect. Nothing was sent.";
      case "CONSENT_WITHDRAWN":
        return "Consent changed while processing. The result was discarded.";
      case "CLOUD_STT_UNAVAILABLE":
        return `${CLOUD_REASON_TEXT[reason] ?? "Online speech-to-text failed."} No transcript was produced and the app did not switch engine. You can retry${caps?.engines.local.languages[language] ? ", choose On-device," : ""} or type instead. (code: ${reason || "unknown"})`;
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
      uploadAbort.current = new AbortController();
      await api.postAudio(`cases/${caseId}/voice/transcriptions?language=${language}&engine=${selected}&idempotency_key=${key}`, wav, uploadAbort.current.signal);
      setPending(null);
      await refresh();
    } catch (err) {
      setMessage(explain(err));
      const fromBackend = err instanceof ApiError && err.code !== "HTTP_ERROR";
      if (fromBackend && err.status < 500 && err.status !== 429 && err.code !== "IN_PROGRESS") setPending(null);
      // A failure the backend stored under this key needs a new key to retry. A network or proxy error may
      // hide a run that finished: keep the key, so a retry returns that result instead of sending again.
      else if (fromBackend && err.code !== "IN_PROGRESS") setPending({ wav, key: crypto.randomUUID() });
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

  if (!caseId || notFound) return <CaseNotFound />;

  const triageOk = caseView?.consent.triage === "granted";
  const canRecord = !!(caseView?.is_creator || caseView?.is_handler) && (role === "patient" || role === "anm");

  return (
    <IntakeShell
      step="voice"
      caseId={caseId}
      role={role}
      triage={caseView?.consent.triage ?? null}
      token={caseView?.patient_token}
      intro="Record the patient describing their symptoms. Measurements that are heard (temperature, pulse, oxygen…) are picked out for checking; symptoms stay in the transcript for the reviewer to read."
    >
      <Notice tone="info">
        <p>
          Voice only <strong>pre-fills</strong> values after a health worker checks each one. It does not decide urgency; the health worker still records triage. A
          value confirmed here can still be wrong if the reading itself was wrong. You can always type instead on the review step.
        </p>
      </Notice>

      {caps && !caps.voice_enabled && <p role="note">Voice input is turned off on this server. Please type the symptoms instead.</p>}

      {caseView && !triageOk && (
        <p role="alert">
          Triage consent is not in effect for this case.{" "}
          <Link href={`/intake/consent?case=${caseId}`} className="text-primary underline">
            Go to consent
          </Link>
        </p>
      )}

      {triageOk && caps?.voice_enabled && (
        <>
          <Card as="section" aria-labelledby="voice-setup" className="space-y-4">
            <h2 id="voice-setup" className="text-lg font-bold">
              Language and speech processing
            </h2>
            <fieldset className="space-y-2">
              <legend className="text-sm font-bold text-muted">Spoken language</legend>
              <div className="inline-flex flex-wrap gap-1 rounded-[16px] bg-surface-1 p-1 shadow-[inset_2px_2px_5px_rgba(0,0,0,0.05),inset_-2px_-2px_5px_rgba(255,255,255,0.7)]">
                {LANGUAGES.map((l) => (
                  <button
                    key={l.code}
                    type="button"
                    onClick={() => setLanguage(l.code)}
                    aria-pressed={language === l.code}
                    className={`min-h-11 rounded-[12px] px-4 py-2 font-bold transition-all duration-300 ${language === l.code ? "bg-[#0891B2] text-white shadow-[4px_4px_10px_rgba(8,145,178,0.2),-4px_-4px_10px_rgba(255,255,255,0.8)]" : "text-ink hover:bg-white/50"}`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset className="space-y-2 text-sm">
              <legend className="text-sm font-bold text-muted">Speech-to-text</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {(["local", "cloud"] as Engine[]).map((e) => {
                  const st = engineStatus(e);
                  return (
                    <label key={e} className={`flex items-start gap-2.5 rounded-[12px] border p-3 transition-all ${selected === e ? "border-[#0891B2] bg-[#0891B2]/5 shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02)]" : "border-white/60 bg-surface-1 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)]"} ${st.usable ? "cursor-pointer hover:-translate-y-0.5 hover:shadow-[6px_6px_15px_0px_rgba(0,0,0,0.05),-6px_-6px_15px_0px_rgba(255,255,255,0.9)]" : "opacity-60 cursor-not-allowed"}`}>
                      <input type="radio" name="engine" className="mt-0.5 size-4" disabled={!st.usable} checked={selected === e} onChange={() => setEngine(e)} />
                      <span>
                        <span className={`flex items-center gap-1.5 font-bold ${st.usable ? "text-ink" : ""}`}>
                          <Icon name={e === "local" ? "lock" : "upload"} size={14} /> {caps.engines[e].label}
                        </span>
                        <span className="block text-muted">{st.why}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
              {!selected && usable.includes("cloud") && <p>Choose “online” above to send this recording to Sarvam AI, or type the symptoms instead.</p>}
              {!selected && !usable.includes("cloud") && (
                <p className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning-bg px-3 py-2 text-ink">
                  <Icon name="info" size={16} className="mt-0.5 text-warning" />
                  No speech-to-text option is available for this language right now. Please type the symptoms instead.
                </p>
              )}
              {selected && (
                <p className={`flex items-start gap-2 rounded-lg border px-3 py-2 font-bold ${selected === "cloud" ? "border-warning/30 bg-warning-bg text-ink" : "border-primary/30 bg-primary-tint/50 text-ink"}`}>
                  <Icon name={selected === "cloud" ? "alert" : "lock"} size={16} className={`mt-0.5 ${selected === "cloud" ? "text-warning" : "text-primary"}`} />
                  {selected === "cloud" ? "Recordings will be sent over the internet to Sarvam AI." : "Recordings are processed on this server; nothing is sent to Sarvam."}
                </p>
              )}
            </fieldset>
          </Card>

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
            <button type="button" onClick={() => upload(pending.wav, pending.key)} className={buttonClass("secondary", "text-sm")}>
              Retry the same recording
            </button>
          )}
          <p aria-live="polite">{busy ? "Processing… (the speech model on this server can take a while on the first run)" : ""}</p>
        </>
      )}

      {message && (
        <p role="alert" className="text-sm text-error">
          {message}
        </p>
      )}

      {items.map((t) => (
        <Card as="article" key={t.transcription_id} className="space-y-3">
          <p className="text-sm">
            <strong>Transcribed by: {t.engine === "local" ? "On this facility\u2019s server (local model)" : "Internet — Sarvam AI"}</strong>
            <span className="text-muted"> · {t.model_id ?? "—"} · {new Date(t.created_at).toLocaleTimeString()}</span>
            {caps && (
              <span className="ml-2 rounded-lg border border-line px-1.5 text-xs">
                {caps.verification[t.engine][t.language] === "tested_real" ? "tried on a few test clips only" : caps.verification[t.engine][t.language] === "tested_mock" ? "not checked on real speech" : "unverified"}
              </span>
            )}
          </p>
          {t.status === "no_speech" && <p>No speech was detected in this recording. Nothing was transcribed.</p>}
          {t.status === "empty_transcript" && <p>The speech could not be turned into text. Please try again or type instead.</p>}
          {t.status === "failed" && <p>This recording failed ({t.failure_code}). No transcript was kept.</p>}
          {t.transcript_raw && (
            <div>
              <h2 className="text-sm font-bold">Raw transcript (not checked)</h2>
              <p lang={t.language} className="whitespace-pre-wrap rounded bg-page p-2">
                <Highlighted text={t.transcript_raw} candidates={t.candidates} />
              </p>
            </div>
          )}
          {t.readback_template_status !== "project_draft" && t.candidates.length > 0 && (
            <p role="note" className="rounded-lg border border-warning min-h-11 px-4 py-2 text-xs">
              Draft translation of the read-back wording — not reviewed by a native speaker. Explain in person if unclear.
            </p>
          )}
          {t.candidates.length > 0 && (
            <ul className="space-y-2">
              {t.candidates.map((c) => (
                <CandidateCard key={c.candidate_id} c={c} language={t.language} reviewer={reviewer} ttsReady={!!caps?.tts.ready && cloudConsent && reviewer} busy={busy} caseId={caseId} onDecide={decide} />
              ))}
            </ul>
          )}
          {t.status === "completed" && t.candidates.length === 0 && <p className="text-sm text-muted">No measurements were recognised. Speak in the language selected above — English speech with Hindi/Odia selected is written phonetically and its numbers are not read. Some spoken number forms (e.g. Odia 21–99 as words) are not read yet — enter them in the triage form.</p>}
        </Card>
      ))}

      {reviewer && prefill && (
        <Card as="aside" className="space-y-2 border-success/30 bg-success-bg/20">
          <h2 className="font-bold">Values the health worker confirmed</h2>
          {Object.keys(prefill.vitals).length + Object.keys(prefill.fields).length === 0 ? (
            <p className="text-sm text-muted">None yet.</p>
          ) : (
            <ul className="text-sm">
              {Object.entries(prefill.values).flatMap(([field, entry]) =>
                Object.entries(entry.values).map(([k, v]) => (
                  <li key={k}>
                    {VITAL_LABEL[k] ?? k}: <strong>{k === "temp_c" && typeof v === "number" ? v.toFixed(2) : String(v)}</strong>{" "}
                    <span className="text-xs text-muted">
                      ({entry.sources.map((s) => `${s.type === "voice_manual_correction" ? "entered by" : "heard, confirmed by"} ${roleWords(s.resolved_by_role)}`).join("; ")}) · {field}
                    </span>
                  </li>
                )),
              )}
            </ul>
          )}
          {prefill.unresolved.length > 0 && (
            <p role="status" className="text-sm">
              {prefill.unresolved.length} heard value(s) not decided yet ({prefill.unresolved.map((u) => `${u.field}${u.state === "unsure" ? " — not sure" : ""}`).join(", ")}). They are not pre-filled.
            </p>
          )}
          {Object.keys(prefill.conflicts).length > 0 && (
            <p role="status" className="text-sm">
              Different values were confirmed for: {Object.keys(prefill.conflicts).join(", ")}. Nothing is pre-filled for these — check with the patient.
            </p>
          )}
          <p className="text-xs text-muted">{prefill.note}</p>
        </Card>
      )}
    </IntakeShell>
  );
}

export default function VoicePage() {
  return (
    <Suspense>
      <VoiceScreen />
    </Suspense>
  );
}
