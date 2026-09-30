"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { ApiError, api } from "@/lib/api";
import { useMatchingVoice } from "@/lib/useMatchingVoice";

type Language = "en" | "hi" | "or";
type State = "not_provided" | "granted" | "declined" | "withdrawn";
type Notice = {
  language: Language;
  version: string;
  review_status: "project_draft" | "draft_unreviewed_translation";
  title: string;
  paragraphs: string[];
  purposes: { triage: string; ai_assist: string; voice_cloud: string };
};
type CaseView = { case_id: string; patient_token: string; scenario: string; is_creator: boolean; consent: { triage: State; ai_assist: State; voice_cloud: State } };

const LANGUAGES: { code: Language; label: string; speech: string }[] = [
  { code: "en", label: "English", speech: "en" },
  { code: "hi", label: "हिन्दी", speech: "hi" },
  { code: "or", label: "ଓଡ଼ିଆ", speech: "or" },
];

const STATE_WORDS: Record<State, string> = {
  not_provided: "No decision yet",
  granted: "Agreed",
  declined: "Did not agree",
  withdrawn: "Withdrew",
};

function ConsentScreen() {
  const caseId = useSearchParams().get("case");
  const [role, setRole] = useState<string | null>(null);
  const [language, setLanguage] = useState<Language>("en");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [caseView, setCaseView] = useState<CaseView | null>(null);
  const [aiOptIn, setAiOptIn] = useState(false);
  const [voiceCloudOptIn, setVoiceCloudOptIn] = useState(false);
  const [attested, setAttested] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const { voice, supported } = useMatchingVoice(LANGUAGES.find((l) => l.code === language)!.speech);

  const loadNotice = useCallback(async (lang: Language) => setNotice(await api.get<Notice>(`consent/notice?language=${lang}`)), []);
  const loadCase = useCallback(async () => {
    if (!caseId) return;
    try {
      setCaseView(await api.get<CaseView>(`cases/${caseId}`));
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 400)) setNotFound(true);
    }
  }, [caseId]);

  useEffect(() => {
    api.get<{ role: string }>("auth/me").then((me) => setRole(me.role));
    loadCase();
  }, [loadCase]);
  useEffect(() => {
    loadNotice(language);
  }, [language, loadNotice]);

  function explain(err: unknown): string {
    if (!(err instanceof ApiError)) return "Something went wrong.";
    const ref = err.requestId ? ` (ref ${err.requestId})` : "";
    switch (err.code) {
      case "NOTICE_VERSION_STALE":
        loadNotice(language);
        return "The notice was updated. Please review the new version and try again.";
      case "NOTHING_TO_WITHDRAW":
        return "There is no consent in effect for that purpose.";
      case "NOT_FOUND":
        setNotFound(true);
        return "Case not found.";
      default:
        return `${err.message}${ref}`;
    }
  }

  async function decide(decision: "grant" | "decline") {
    if (!notice || !caseId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/consent`, { decision, include_ai_assist: decision === "grant" && aiOptIn, include_voice_cloud: decision === "grant" && voiceCloudOptIn, language, notice_version: notice.version });
      await loadCase();
    } catch (err) {
      setMessage(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(purpose: "triage" | "ai_assist" | "voice_cloud") {
    if (!caseId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/consent/withdraw`, { purpose });
      await loadCase();
    } catch (err) {
      setMessage(explain(err));
    } finally {
      setBusy(false);
    }
  }

  function readAloud() {
    if (!notice || !voice) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance([notice.title, ...notice.paragraphs, notice.purposes.triage].join(". "));
    utterance.voice = voice;
    utterance.lang = voice.lang;
    window.speechSynthesis.speak(utterance);
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

  const isAnm = role === "anm";
  const canRecord = caseView?.is_creator && (role === "patient" || role === "anm");
  const triageState = caseView?.consent.triage ?? "not_provided";
  const aiState = caseView?.consent.ai_assist ?? "not_provided";
  const voiceCloudState = caseView?.consent.voice_cloud ?? "not_provided";

  return (
    <section className="mx-auto max-w-2xl space-y-5">
      <DemoBanner />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Consent</h1>
        <span className="font-mono text-xs opacity-70">Case {caseView?.patient_token ?? "…"}</span>
      </div>

      <fieldset className="flex flex-wrap gap-2" aria-label="Notice language">
        {LANGUAGES.map((l) => (
          <button
            key={l.code}
            type="button"
            onClick={() => setLanguage(l.code)}
            aria-pressed={language === l.code}
            className={`rounded border px-3 py-1 ${language === l.code ? "border-blue-600 bg-blue-50 dark:bg-blue-950" : "border-black/20 dark:border-white/20"}`}
          >
            {l.label}
          </button>
        ))}
      </fieldset>

      {notice?.review_status === "draft_unreviewed_translation" && (
        <p role="note" className="rounded border border-orange-500 px-3 py-2 text-sm">
          Draft translation — not reviewed by a native speaker. Explain the notice in person if anything is unclear.
        </p>
      )}

      {notice && (
        <article lang={notice.language} className="space-y-3 rounded border border-black/10 p-4 dark:border-white/15">
          <h2 className="text-lg font-semibold">{notice.title}</h2>
          {notice.paragraphs.map((p) => (
            <p key={p}>{p}</p>
          ))}
          <p className="text-xs opacity-60">Notice version {notice.version}</p>
        </article>
      )}

      <div className="space-y-1">
        <button type="button" onClick={readAloud} disabled={!voice} className="rounded border border-black/20 px-3 py-1 text-sm disabled:opacity-50 dark:border-white/20">
          🔊 Read aloud
        </button>
        {(!supported || !voice) && (
          <p className="text-sm opacity-80">
            Read-aloud is not available for this language on this device. Please read the notice to the patient.
          </p>
        )}
      </div>

      <div className="rounded border border-black/10 p-4 dark:border-white/15">
        <p className="text-sm">
          Triage consent: <strong>{STATE_WORDS[triageState]}</strong> · AI assistance: <strong>{STATE_WORDS[aiState]}</strong> · Online speech (Sarvam): <strong>{STATE_WORDS[voiceCloudState]}</strong>
        </p>
      </div>

      {canRecord && triageState !== "granted" && notice && (
        <div className="space-y-3">
          <p className="font-medium">{notice.purposes.triage}</p>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={aiOptIn} onChange={(e) => setAiOptIn(e.target.checked)} />
            <span>
              {notice.purposes.ai_assist}
              <span className="block opacity-70">AI assistance works with English text only in this prototype.</span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={voiceCloudOptIn} onChange={(e) => setVoiceCloudOptIn(e.target.checked)} />
            <span>
              {notice.purposes.voice_cloud}
              <span className="block opacity-70">Optional. Without it, speech can only be processed on this device (if available), or symptoms can be typed.</span>
            </span>
          </label>
          {isAnm && (
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
              <span>I read this notice to the patient in their language and they said yes (haan). This records my attestation; no audio is stored.</span>
            </label>
          )}
          <div className="flex flex-wrap gap-3">
            <button type="button" disabled={busy || (isAnm && !attested)} onClick={() => decide("grant")} className="rounded bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-60">
              {isAnm ? "Record patient's agreement" : "I agree"}
            </button>
            <button type="button" disabled={busy} onClick={() => decide("decline")} className="rounded border border-black/20 px-4 py-2 dark:border-white/20">
              {isAnm ? "Patient did not agree" : "I do not agree"}
            </button>
          </div>
        </div>
      )}

      {canRecord && triageState === "granted" && (
        <div className="flex flex-wrap gap-3">
          <Link href={`/intake/voice?case=${caseId}`} className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white">
            Next: describe symptoms by voice →
          </Link>
          {voiceCloudState === "granted" && (
            <button type="button" disabled={busy} onClick={() => withdraw("voice_cloud")} className="rounded border border-black/20 px-4 py-2 text-sm dark:border-white/20">
              Stop online speech processing (keep triage consent)
            </button>
          )}
          {aiState === "granted" && (
            <button type="button" disabled={busy} onClick={() => withdraw("ai_assist")} className="rounded border border-black/20 px-4 py-2 text-sm dark:border-white/20">
              Stop AI assistance (keep triage consent)
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => withdraw("triage")} className="rounded border border-red-600 px-4 py-2 text-sm text-red-700 dark:text-red-400">
            Withdraw all consent (stops triage, AI assistance and online speech)
          </button>
        </div>
      )}

      {caseView && !canRecord && <p className="text-sm opacity-70">Only the account that started this case can record or withdraw consent.</p>}

      {message && (
        <p role="alert" className="text-sm text-red-600">
          {message}
        </p>
      )}
    </section>
  );
}

export default function ConsentPage() {
  return (
    <Suspense>
      <ConsentScreen />
    </Suspense>
  );
}
