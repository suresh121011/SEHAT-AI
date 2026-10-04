"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { roleWords } from "@/components/Provenance";
import { Button, Card, Notice, Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { clearNotes } from "@/lib/intakeStore";
import type { ConsentState } from "@/lib/steps";
import { useCase } from "@/lib/useCase";
import { useMatchingVoice } from "@/lib/useMatchingVoice";

type Language = "en" | "hi" | "or";
type Purpose = "triage" | "ai_assist" | "voice_cloud";
type Notice = {
  language: Language;
  version: string;
  review_status: "project_draft" | "draft_unreviewed_translation";
  title: string;
  paragraphs: string[];
  purposes: Record<Purpose, string>;
};
type HistoryRow = { seq: number; purpose: Purpose; action: string; method: string; actor_role: string; language: Language; notice_version: string; created_at: string };

const LANGUAGES: { code: Language; label: string; speech: string }[] = [
  { code: "en", label: "English", speech: "en" },
  { code: "hi", label: "हिन्दी", speech: "hi" },
  { code: "or", label: "ଓଡ଼ିଆ", speech: "or" },
];

const STATE_WORDS: Record<ConsentState, string> = { not_provided: "No decision yet", granted: "Agreed", declined: "Did not agree", withdrawn: "Withdrawn" };
const PURPOSE_NAME: Record<Purpose, string> = { triage: "Triage (required)", ai_assist: "AI assistance (optional)", voice_cloud: "Online speech service (optional)" };
const METHOD_WORDS: Record<string, string> = {
  patient_button: "patient pressed “I agree” on their account",
  staff_attested_verbal: "health worker attested the patient said yes",
  cascade_from_triage: "withdrawn together with triage consent",
};

function ConsentScreen() {
  const { caseId, role, caseView, notFound, loadError, reload } = useCase();
  const [language, setLanguage] = useState<Language>("en");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [aiOptIn, setAiOptIn] = useState(false);
  const [voiceCloudOptIn, setVoiceCloudOptIn] = useState(false);
  const [attested, setAttested] = useState(false);
  const [message, setMessage] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmWithdraw, setConfirmWithdraw] = useState<Purpose | null>(null);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const messageRef = useRef<HTMLDivElement>(null);
  const { voice, supported } = useMatchingVoice(LANGUAGES.find((l) => l.code === language)!.speech);

  // Any change of language or notice version resets every choice: consent is given to the text actually shown.
  const resetChoices = useCallback(() => {
    setAiOptIn(false);
    setVoiceCloudOptIn(false);
    setAttested(false);
  }, []);
  const loadNotice = useCallback(
    async (lang: Language) => {
      try {
        setNotice(await api.get<Notice>(`consent/notice?language=${lang}`));
        resetChoices();
      } catch {
        setMessage({ tone: "error", text: "Could not load the consent notice. Check the connection and try again." });
      }
    },
    [resetChoices],
  );
  useEffect(() => {
    loadNotice(language);
  }, [language, loadNotice]);

  const loadHistory = useCallback(async () => {
    if (!caseId) return;
    try {
      setHistory((await api.get<{ history: HistoryRow[] }>(`cases/${caseId}/consent`)).history);
    } catch {
      setHistory(null);
    }
  }, [caseId]);
  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  if (notFound) return <CaseNotFound />;

  function show(tone: "error" | "success", text: string) {
    setMessage({ tone, text });
    requestAnimationFrame(() => messageRef.current?.focus());
  }

  function explain(err: unknown): string {
    if (!(err instanceof ApiError)) return "Could not reach the server. Nothing was recorded; try again.";
    switch (err.code) {
      case "NOTICE_VERSION_STALE":
        loadNotice(language);
        return "The notice was updated. Please read the new version with the patient and choose again. Nothing was recorded.";
      case "NOTHING_TO_WITHDRAW":
        return "There is no consent in effect for that purpose.";
      default:
        return `${err.message}${err.requestId ? ` (reference ${err.requestId})` : ""}`;
    }
  }

  async function decide(decision: "grant" | "decline") {
    if (!notice || !caseId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/consent`, { decision, include_ai_assist: decision === "grant" && aiOptIn, include_voice_cloud: decision === "grant" && voiceCloudOptIn, language, notice_version: notice.version });
      await reload();
      await loadHistory();
      show("success", decision === "grant" ? "Consent recorded." : "Recorded that the patient did not agree. Nothing will be processed for this case.");
    } catch (err) {
      show("error", explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(purpose: Purpose) {
    if (!caseId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/consent/withdraw`, { purpose });
      if (purpose === "triage") clearNotes(caseId); // local notes go with the withdrawal
      await reload();
      await loadHistory();
      show("success", "Withdrawal recorded. Further processing for that purpose has stopped. Information already recorded is kept; deleting it is not available in this prototype.");
    } catch (err) {
      show("error", explain(err));
    } finally {
      setBusy(false);
      setConfirmWithdraw(null);
    }
  }

  function readAloud() {
    if (!notice || !voice) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance([notice.title, ...notice.paragraphs, notice.purposes.triage, notice.purposes.ai_assist, notice.purposes.voice_cloud].join(". "));
    u.voice = voice;
    u.lang = voice.lang;
    window.speechSynthesis.speak(u);
  }

  const isAnm = role === "anm";
  const canRecord = !!caseView?.is_creator && (role === "patient" || role === "anm");
  const triage = caseView?.consent.triage ?? null;
  const aiState = caseView?.consent.ai_assist ?? "not_provided";
  const voiceState = caseView?.consent.voice_cloud ?? "not_provided";
  const agreedPurposes = ["triage", aiOptIn && "AI assistance", voiceCloudOptIn && "the online speech service"].filter(Boolean).join(", ");

  return (
    <IntakeShell
      step="consent"
      caseId={caseId}
      role={role}
      triage={triage}
      token={caseView?.patient_token}
      scenario={caseView?.scenario}
      intro={isAnm ? "Read the notice to the patient in their language, answer their questions, then record their choices." : "Please read this notice. You can say no to the optional parts."}
      nextReady={triage === "granted"}
      nextHint="Available after the patient agrees to triage."
    >
      {loadError && (
        <Notice tone="error" role="alert">
          {loadError}
        </Notice>
      )}
      {message && (
        <div ref={messageRef} tabIndex={-1} className="outline-none">
          <Notice tone={message.tone} role={message.tone === "error" ? "alert" : "status"}>
            <p>{message.text}</p>
          </Notice>
        </div>
      )}

      <fieldset>
        <legend className="font-bold">Notice language</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {LANGUAGES.map((l) => (
            <label key={l.code} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded border-2 px-4 ${language === l.code ? "border-primary bg-primary-tint font-bold" : "border-line bg-card"}`}>
              <input type="radio" name="lang" className="size-4" checked={language === l.code} onChange={() => setLanguage(l.code)} />
              <span lang={l.code}>{l.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      {notice?.review_status === "draft_unreviewed_translation" && (
        <Notice tone="warning" title="Draft translation">
          Not yet reviewed by a native speaker. Explain the notice in person if anything is unclear.
        </Notice>
      )}

      {!notice ? (
        <Spinner label="Loading the notice…" />
      ) : (
        <article lang={notice.language} className="space-y-3 rounded-lg border border-consent/30 bg-consent-bg p-5 text-ink">
          <h2 className="flex items-center gap-2 text-2xl font-bold text-consent">
            <Icon name="shield" size={26} /> {notice.title}
          </h2>
          {notice.paragraphs.map((p) => (
            <p key={p}>{p}</p>
          ))}
          <p className="text-sm text-muted">Notice version {notice.version}</p>
        </article>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={readAloud} disabled={!voice}>
          <Icon name="speaker" /> Read aloud
        </Button>
        {(!supported || !voice) && <p className="text-muted">Read-aloud is not available for this language on this device. Please read the notice to the patient.</p>}
      </div>

      <Card>
        <h2 className="text-xl font-bold">Current consent</h2>
        <dl className="mt-2 grid gap-2 sm:grid-cols-3">
          {(["triage", "ai_assist", "voice_cloud"] as Purpose[]).map((p) => {
            const st = caseView?.consent[p] ?? "not_provided";
            return (
              <div key={p} className="rounded border border-subtle p-3">
                <dt className="text-sm text-muted">{PURPOSE_NAME[p]}</dt>
                <dd className="flex items-center gap-1 font-bold">
                  <Icon name={st === "granted" ? "check" : st === "not_provided" ? "info" : "cross"} size={18} />
                  {STATE_WORDS[st]}
                </dd>
              </div>
            );
          })}
        </dl>
      </Card>

      {canRecord && triage !== "granted" && notice && (
        <Card>
          <h2 className="text-xl font-bold">Choices</h2>
          <div className="mt-3 space-y-3">
            <div className="flex items-start gap-3 rounded border-2 border-primary p-3">
              <Icon name="lock" className="mt-1 text-primary" />
              <p>
                <strong>Required: </strong>
                <span lang={notice.language}>{notice.purposes.triage}</span>
              </p>
            </div>
            <label className="flex min-h-11 items-start gap-3 rounded border border-line p-3">
              <input type="checkbox" className="mt-1 size-5" checked={aiOptIn} onChange={(e) => setAiOptIn(e.target.checked)} />
              <span>
                <strong>Optional: </strong>
                <span lang={notice.language}>{notice.purposes.ai_assist}</span>
                <span className="block text-sm text-muted">AI assistance works with English text only in this prototype. Saying no does not stop triage.</span>
              </span>
            </label>
            <label className="flex min-h-11 items-start gap-3 rounded border border-line p-3">
              <input type="checkbox" className="mt-1 size-5" checked={voiceCloudOptIn} onChange={(e) => setVoiceCloudOptIn(e.target.checked)} />
              <span>
                <strong>Optional: </strong>
                <span lang={notice.language}>{notice.purposes.voice_cloud}</span>
                <span className="block text-sm text-muted">Without it, speech is only processed on this facility&apos;s server (if available), or symptoms can be typed.</span>
              </span>
            </label>
            {isAnm && (
              <label className="flex min-h-11 items-start gap-3 rounded border-2 border-secondary bg-info-bg p-3">
                <input type="checkbox" className="mt-1 size-5" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
                <span>
                  I read this notice to the patient in their language, and they said yes to: <strong>{agreedPurposes}</strong>. This records my attestation as the
                  health worker; it does not prove the patient&apos;s identity, and no audio is stored.
                </span>
              </label>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button disabled={busy || (isAnm && !attested)} onClick={() => decide("grant")}>
              {isAnm ? "Record the patient’s agreement" : "I agree"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => decide("decline")}>
              {isAnm ? "Patient did not agree" : "I do not agree"}
            </Button>
          </div>
          {isAnm && !attested && <p className="mt-2 text-sm text-muted">Tick the attestation above to record agreement.</p>}
        </Card>
      )}

      {canRecord && triage === "granted" && (
        <Card>
          <h2 className="text-xl font-bold">Change consent</h2>
          <p className="text-muted">
            Withdrawing stops further processing for that purpose. Information already recorded is kept; deleting it is not available in this prototype. To add an optional part later,
            withdraw triage consent and record consent again.
          </p>
          <div className="mt-3 flex flex-wrap gap-3">
            {voiceState === "granted" && (
              <Button variant="secondary" disabled={busy} onClick={() => setConfirmWithdraw("voice_cloud")}>
                Stop online speech processing
              </Button>
            )}
            {aiState === "granted" && (
              <Button variant="secondary" disabled={busy} onClick={() => setConfirmWithdraw("ai_assist")}>
                Stop AI assistance
              </Button>
            )}
            <Button variant="danger" disabled={busy} onClick={() => setConfirmWithdraw("triage")}>
              Withdraw all consent
            </Button>
          </div>
          {confirmWithdraw && (
            <div role="alertdialog" aria-labelledby="wd-title" aria-describedby="wd-text" className="mt-4 rounded-lg border-2 border-error bg-error-bg p-4">
              <p id="wd-title" className="font-bold text-error">
                Withdraw {confirmWithdraw === "triage" ? "all consent (triage, AI assistance and online speech)" : PURPOSE_NAME[confirmWithdraw].replace(" (optional)", "")}?
              </p>
              <p id="wd-text">Processing stops now. Information already recorded is kept; deleting it is not available in this prototype.</p>
              <div className="mt-3 flex flex-wrap gap-3">
                <Button variant="danger" disabled={busy} onClick={() => withdraw(confirmWithdraw)} autoFocus>
                  Yes, withdraw
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => setConfirmWithdraw(null)}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {caseView && !canRecord && <Notice tone="info">Only the account that started this case can record or withdraw consent.</Notice>}

      {history && history.length > 0 && (
        <details className="rounded-lg border border-subtle bg-card p-4">
          <summary className="min-h-11 cursor-pointer py-2 font-bold">Consent record ({history.length} entries)</summary>
          <ol className="mt-2 space-y-1 text-sm">
            {history.map((h) => (
              <li key={h.seq}>
                {new Date(h.created_at).toLocaleString()}: {PURPOSE_NAME[h.purpose] ?? h.purpose}: <strong>{h.action}</strong> ({METHOD_WORDS[h.method] ?? h.method}; by {roleWords(h.actor_role)}; notice {h.notice_version}, {h.language})
              </li>
            ))}
          </ol>
        </details>
      )}
    </IntakeShell>
  );
}

export default function ConsentPage() {
  return (
    <Suspense>
      <ConsentScreen />
    </Suspense>
  );
}
