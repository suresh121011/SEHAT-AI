"use client";

import { Suspense, useEffect, useState } from "react";

import { BodyMap } from "@/components/BodyMap";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { Notice, Spinner } from "@/components/ui";
import { sanitize } from "@/lib/bodyMap";
import { readNotes, writeNotes } from "@/lib/intakeStore";
import { hrefFor } from "@/lib/steps";
import { useCase } from "@/lib/useCase";

function BodyMapScreen() {
  const { caseId, role, caseView, notFound, loadError } = useCase();
  const [selected, setSelected] = useState<string[] | null>(null);

  useEffect(() => {
    if (caseId) setSelected(sanitize(readNotes(caseId).bodyMap));
  }, [caseId]);

  if (notFound) return <CaseNotFound />;
  const triage = caseView?.consent.triage ?? null;

  function change(next: string[]) {
    if (!caseId) return;
    setSelected(next);
    writeNotes(caseId, { ...readNotes(caseId), bodyMap: next });
  }

  return (
    <IntakeShell
      step="body"
      caseId={caseId}
      role={role}
      triage={triage}
      token={caseView?.patient_token}
      scenario={caseView?.scenario}
      intro="Show where you feel pain or a problem. Choose as many areas as you need."
    >
      {loadError && (
        <Notice tone="error" role="alert">
          {loadError}
        </Notice>
      )}
      {caseView && triage !== "granted" ? (
        <Notice tone="warning" title="Consent needed first">
          Triage consent is not in effect. <a href={hrefFor("consent", caseId)}>Go to consent</a>.
        </Notice>
      ) : selected === null || !caseView ? (
        <Spinner label="Loading…" />
      ) : (
        <>
          <Notice tone="info" title="Kept on this device only">
            <p>
              These areas are what the patient points to, not a diagnosis. They are a note for the health worker on the review step. They are not saved
              to the case and not sent anywhere, and they are cleared when triage is submitted, consent is withdrawn, or you log out.
            </p>
          </Notice>
          <BodyMap selected={selected} onChange={change} />
        </>
      )}
    </IntakeShell>
  );
}

export default function BodyMapPage() {
  return (
    <Suspense>
      <BodyMapScreen />
    </Suspense>
  );
}
