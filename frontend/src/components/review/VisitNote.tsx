"use client";

// The encounter summary note on the health worker's final step (docs/plans/clinical-note-v2-plan.md step 2).
// Built once when triage has been submitted, from everything recorded for the visit, and shown read-only: this is
// what the medical officer will open. Nothing here accepts the summary, signs off or changes urgency. The health
// worker who created the visit is allowed to build it (backend: creator ANM or medical officer).
import { useCallback, useEffect, useState } from "react";

import { Icon } from "@/components/Icon";
import { NoteDocument } from "@/components/review/EncounterSummaryPanel";
import { Button, Card, Notice, Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { noteSections, openItems, openItemsWords, reviewWords, type SummaryView } from "@/lib/encounterSummary";

function explain(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "The summary could not be built: triage consent is not in effect, or this account did not create the visit.";
    return `The summary could not be built (${err.message}). The triage result above is saved; the medical officer can still build the summary.`;
  }
  return "The summary could not be built. Check the connection and try again. The triage result above is saved.";
}

export function VisitNote({ caseId, triageRunId }: { caseId: string; triageRunId: string }) {
  const [view, setView] = useState<SummaryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const build = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setView({ ...(await api.post<SummaryView>(`cases/${caseId}/encounter-summary`)), case_id: caseId });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }, [caseId]);

  // A new triage run is new evidence: build again for it (the server reuses the stored summary when nothing changed).
  useEffect(() => {
    void build();
  }, [build, triageRunId]);

  const s = view?.summary ?? null;
  const open = s ? openItemsWords(openItems(s)) : [];

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">Summary note for the medical officer</h2>
          <p className="text-sm text-muted">Put together from everything recorded for this visit. A draft: the medical officer reviews it. It is not a diagnosis and it never changes the urgency above.</p>
        </div>
        {s && !busy && (
          <Button variant="quiet" onClick={() => void build()}>
            <Icon name="refresh" size={16} /> Build again
          </Button>
        )}
      </div>

      {busy && <div className="mt-3"><Spinner label="Building the summary note…" /></div>}
      {error && !busy && (
        <Notice tone="error" role="alert" className="mt-3">
          <p>{error}</p>
          <Button variant="secondary" className="mt-1" onClick={() => void build()}>
            <Icon name="refresh" size={16} /> Try again
          </Button>
        </Notice>
      )}

      {s && !busy && (
        <div className="mt-3 space-y-3">
          {open.length > 0 ? (
            <Notice tone="warning" role="status" title="Still needs a person before this note can be relied on">
              <ul className="list-disc pl-5" data-testid="visit-note-open-items">
                {open.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </Notice>
          ) : (
            <Notice tone="info" role="status">Nothing is waiting to be checked. That does not mean the record is complete: the medical officer still reviews the note.</Notice>
          )}
          {view?.is_stale && <Notice tone="warning" role="alert" title="Out of date">{view.stale_message} Press “Build again”.</Notice>}
          {noteSections(s).length > 0 ? (
            <NoteDocument s={s} reviewText={reviewWords(view?.review?.status).text} stale={Boolean(view?.is_stale)} />
          ) : (
            <p className="text-sm text-muted">This summary was made by an older version and has no note. Press “Build again”.</p>
          )}
        </div>
      )}
    </Card>
  );
}
