// Document verification state shared by the ANM's documents step and the medical officer's verification page: the
// case's documents, the "What to check, in order" queue, the open row, auto-advance after a decision, and deep links
// (`?field=` opens a row, `?doc=` goes to its patient question). Moved unchanged from app/intake/documents/page.tsx.
// The backend decides access (case, facility, consent); this hook only reads and reloads.
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Doc } from "@/lib/documentReview";
import { nextDocumentField, type Queue, type QueueItem } from "@/lib/verificationQueue";

export type Selected = { doc: string; field: string } | null;

export function useDocumentVerification(caseId: string | null, reviewer: boolean, enabled: boolean, focus: { field: string | null; doc: string | null }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [selected, setSelected] = useState<Selected>(null);
  const [focused, setFocused] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);

  const refresh = useCallback(async (): Promise<Queue | null> => {
    if (!caseId || !enabled) {
      setDocs([]);
      setQueue(null);
      return null;
    }
    let q: Queue | null = null;
    try {
      setDocs((await api.get<{ documents: Doc[] }>(`cases/${caseId}/documents`)).documents.reverse());
      // "Check these first" (P0-2). An older backend answers 404: then the page works as before, without the order.
      if (reviewer) q = await api.get<Queue>(`cases/${caseId}/verification-queue`).catch(() => null);
      setLoadError(null);
    } catch (err) {
      setLoadError(err);
    }
    setQueue(q);
    return q;
  }, [caseId, reviewer, enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // After a recorded decision, open the next row to check (highest priority first); after an error, stay.
  const afterDecision = useCallback(async (decidedFieldId: string | null) => {
    const q = await refresh();
    if (!decidedFieldId || !q) return;
    const next = nextDocumentField(q.items, decidedFieldId);
    if (next) {
      setSelected(next);
      document.getElementById(`field-${next.field}`)?.scrollIntoView({ block: "nearest" });
    }
  }, [refresh]);

  const openQueueItem = useCallback((i: QueueItem) => {
    if (i.kind === "document_field" && i.ref.document_id && i.ref.field_id) {
      setSelected({ doc: i.ref.document_id, field: i.ref.field_id });
      document.getElementById(`field-${i.ref.field_id}`)?.scrollIntoView({ block: "center" });
    } else if (i.ref.document_id) {
      document.getElementById(`attest-${i.ref.document_id}`)?.scrollIntoView({ block: "center" });
    }
  }, []);

  useEffect(() => {
    if (focused || docs.length === 0 || (!focus.field && !focus.doc)) return;
    setFocused(true);
    const d = docs.find((x) => (focus.field ? x.fields.some((f) => f.field_id === focus.field) : x.document_id === focus.doc));
    if (!d) return;
    if (focus.field) {
      const field = focus.field;
      setSelected({ doc: d.document_id, field });
      requestAnimationFrame(() => document.getElementById(`field-${field}`)?.scrollIntoView({ block: "center" }));
    } else {
      requestAnimationFrame(() => document.getElementById(`attest-${d.document_id}`)?.scrollIntoView({ block: "center" }));
    }
  }, [docs, focus.field, focus.doc, focused]);

  return { docs, queue, selected, setSelected, refresh, afterDecision, openQueueItem, loadError };
}
