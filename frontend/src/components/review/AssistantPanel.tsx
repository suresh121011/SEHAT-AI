"use client";

// The fenced clinical assistant on the medical officer's case screen (docs/24; plan step 5). Four fixed questions, no
// typing. Each answer shows only the sentences that passed the server's gate, each with what it cites; the dropped
// ones are counted by reason, and the tools the model called are listed. Display only: nothing here sets or changes
// urgency, and the rules engine's result stays in "Why the rules gave …".
import { useEffect, useState } from "react";

import { ProvenanceBadge } from "@/components/review/Badges";
import { SectionCard } from "@/components/review/Panel";
import { Button, Notice } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { droppedLine, statusWords, toolLine, unavailableWords, type AssistantAnswer, type AssistantView } from "@/lib/assistant";

export function AssistantPanel({ caseId }: { caseId: string }) {
  const [view, setView] = useState<AssistantView | null>(null);
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api.get<AssistantView>(`cases/${caseId}/assistant`, { signal: ctrl.signal }).then(
      (v) => {
        setView(v);
        setAnswer(v.answers[0] ?? null);
        setError(null);
      },
      (err) => {
        if (!ctrl.signal.aborted) setError(err instanceof ApiError ? err.message : "The server could not be reached. Try again.");
      },
    );
    return () => ctrl.abort();
  }, [caseId]);

  async function ask(questionId: string) {
    setBusy(questionId);
    setError(null);
    try {
      setAnswer(await api.post<AssistantAnswer>(`cases/${caseId}/assistant/answers`, { question_id: questionId }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The server could not be reached. Try again.");
    } finally {
      setBusy(null);
    }
  }

  const blocked = !view || !view.available || view.consent_needed;
  return (
    <SectionCard id={`assistant-${caseId}`} title="Assistant (AI-written, read-only)" icon="sparkle"
                 aside={<ProvenanceBadge kind="ai_suggested" detail={view?.model ?? undefined} />}>
      {!view && !error && <p className="text-sm text-muted">Loading the assistant…</p>}
      {view && <p className="text-xs text-muted">{view.notice}</p>}
      {view && !view.available && (
        <Notice tone="warning">
          The assistant is not available: {unavailableWords(view.reason)}. Nothing is sent to a model, and no other answer is given.
        </Notice>
      )}
      {view?.consent_needed && (
        <Notice tone="info">There is no consent to AI assistance for this visit, so the assistant cannot be used and stored answers are hidden.</Notice>
      )}
      {error && (
        <Notice tone="error" role="alert">
          {error}
        </Notice>
      )}
      {view && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="Questions for the assistant">
          {view.questions.map((q) => (
            <Button key={q.id} variant="secondary" disabled={blocked || busy !== null} onClick={() => void ask(q.id)}>
              {busy === q.id ? "Asking…" : q.text}
            </Button>
          ))}
        </div>
      )}
      {busy && view && <p className="text-xs text-muted" role="status">Asking the model. This can take up to {view.limits.timeout_s} seconds.</p>}
      {answer && view && !view.consent_needed && (
        <div className="space-y-2 rounded-lg border border-subtle p-3" aria-live="polite">
          <p className="font-bold">{answer.question}</p>
          {answer.reused && <p className="text-xs text-muted">Stored answer: the record has not changed since it was written, so the model was not asked again.</p>}
          {answer.is_stale && <Notice tone="warning">The record changed after this answer was written. Ask again before relying on it.</Notice>}
          {statusWords(answer, view.limits.max_tool_calls) && <Notice tone="warning">{statusWords(answer, view.limits.max_tool_calls)}</Notice>}
          {answer.status === "answered" && answer.sentences.length === 0 && <p className="text-sm">The model found nothing to add from the record.</p>}
          {answer.sentences.length > 0 && (
            <ul className="space-y-2">
              {answer.sentences.map((s, i) => (
                <li key={i} className="border-l-4 border-primary pl-2 text-sm">
                  <p>{s.text}</p>
                  <p className="text-xs text-muted">From: {s.cites.map((c) => c.label).join("; ")}</p>
                </li>
              ))}
            </ul>
          )}
          {droppedLine(answer.dropped) && <p className="text-xs">{droppedLine(answer.dropped)}</p>}
          <p className="text-xs text-muted">{toolLine(answer.tools)}</p>
          <p className="text-xs text-muted">
            Model {answer.model} · prompt {answer.prompt_version}
            {answer.tokens.prompt !== null && ` · ${answer.tokens.prompt + (answer.tokens.completion ?? 0)} tokens`}
          </p>
        </div>
      )}
    </SectionCard>
  );
}
