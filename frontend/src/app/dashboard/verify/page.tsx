"use client";

// Medical officer: check a case's machine-read document values against the page image. The SAME review experience as
// the ANM's documents step — "What to check, in order", the patient question, each row with confirm / correct / not
// sure / reject and keys C/U/R — through the same components and the same endpoints, which enforce case access,
// facility scope (ACCOUNT_FACILITIES), consent and audit. No upload and no delete here. Never changes urgency; a value
// a person checks is still entered on the triage form by a person.
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { DOC_TYPE_LABEL } from "@/components/DocumentUpload";
import { CheckFirstPanel } from "@/components/review/CheckFirstPanel";
import { DocumentChecks, ENGINE_LABEL, explain } from "@/components/review/DocumentChecks";
import { LoadingState } from "@/components/review/States";
import { ButtonLink, Card, Notice, PageHeader } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { Doc } from "@/lib/documentReview";
import { useDocumentVerification } from "@/lib/useDocumentVerification";
import { useMe } from "@/lib/useReview";

type CaseView = { patient_token: string; consent: { triage: string } };

export default function VerifyPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading the documents…" />}>
      <VerifyScreen />
    </Suspense>
  );
}

function VerifyScreen() {
  const params = useSearchParams();
  const caseId = params.get("case");
  const { me } = useMe();
  const reviewer = me?.role === "medical_officer";
  const [caseView, setCaseView] = useState<CaseView | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const v = useDocumentVerification(caseId, reviewer, caseView?.consent.triage === "granted", { field: params.get("field"), doc: params.get("doc") });

  useEffect(() => {
    if (!caseId) return;
    api.get<CaseView>(`cases/${caseId}`).then(setCaseView, (err) =>
      // Another facility's case answers exactly like a missing one (backend consent.load_case): nothing is revealed.
      setProblem(err instanceof ApiError && err.status === 404 ? "This case was not found, or it is outside your facilities." : explain(err)));
  }, [caseId]);

  const attest = useCallback(async (doc: Doc, answer: string) => {
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/documents/${doc.document_id}/attestation`, { answer, supersedes: doc.attestation?.event_id ?? null });
    } catch (err) {
      setMessage(explain(err));
    }
    await v.refresh();
  }, [caseId, v.refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = (() => {
    const d = v.docs.find((x) => x.document_id === v.selected?.doc) ?? v.docs.find((x) => x.status === "completed");
    const f = d?.fields.find((x) => x.field_id === v.selected?.field) ?? d?.fields[0];
    return d && f ? { doc: d.document_id, field: f.field_id } : null;
  })();

  if (!caseId) return <Notice tone="warning">Open a case from the review queue first.</Notice>;
  const back = <ButtonLink variant="secondary" href={`/dashboard?case=${encodeURIComponent(caseId)}`}>Back to the case</ButtonLink>;
  if (problem) return <div className="space-y-3"><Notice tone="error" role="alert">{problem}</Notice>{back}</div>;
  if (!caseView || !me) return <LoadingState label="Loading the documents…" />;
  const triageOk = caseView.consent.triage === "granted";

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow={`Case ${caseView.patient_token}`}
        title="Check document values"
        description="Compare each machine-read value with the highlighted place on the page image, then confirm, type the right value, mark it not sure, or reject it."
        actions={back}
      />
      <Notice tone="info">
        <p>
          Machine-read and auto-checked — <strong>not yet confirmed by a person</strong>. When the OCR engines agree, that is a machine cross-check,
          not a check by you. Your decisions are recorded with your role; the machine reading is kept unchanged. Checked values <strong>never
          change triage or urgency</strong>: a person enters any value on the triage form.
        </p>
      </Notice>
      {!triageOk && <Notice tone="warning" role="alert">Triage consent is not in effect for this case, so its documents cannot be shown.</Notice>}
      {message && <p role="alert" className="text-sm text-error">{message}</p>}
      {triageOk && <CheckFirstPanel caseId={caseId} queue={v.queue} selectedFieldId={current?.field ?? null} onOpen={v.openQueueItem} voiceLinks={false} />}
      {triageOk && v.docs.length === 0 && <p className="text-sm text-muted">No documents have been uploaded for this case.</p>}
      {triageOk && v.docs.map((d) => (
        <Card as="article" key={d.document_id} className="space-y-3">
          <p className="text-sm">
            <strong>{DOC_TYPE_LABEL[d.document_type]}</strong> · read by {Object.entries(d.engines).map(([e, s]) => `${ENGINE_LABEL[e] ?? e}: ${s === "ok" ? "done" : s.replaceAll("_", " ")}`).join(" · ")}
            <span className="text-muted"> · uploaded {new Date(d.created_at).toLocaleString()}</span>
          </p>
          {d.status === "deleted" && <p role="note">Deleted — page images, read text and decisions were removed from this server.</p>}
          {d.status !== "completed" && d.status !== "deleted" && (
            <p role="alert">This document was not read ({d.status.replaceAll("_", " ")}). Nothing was extracted.</p>
          )}
          {d.status === "completed" && (
            <DocumentChecks d={d} caseId={caseId} reviewer={reviewer} selected={current} onSelect={v.setSelected} onAttest={attest}
              onDecided={v.afterDecision} setMessage={setMessage} />
          )}
        </Card>
      ))}
    </div>
  );
}
