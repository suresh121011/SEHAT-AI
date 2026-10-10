"use client";

import { Suspense, useEffect, useRef, useState } from "react";

import { BodyMap } from "@/components/BodyMap";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { Notice, Spinner } from "@/components/ui";
import { sanitize } from "@/lib/bodyMap";
import { api } from "@/lib/api";
import { hrefFor } from "@/lib/steps";
import { explainError, useCase } from "@/lib/useCase";

type BodyMapView = { regions: string[] };

function BodyMapScreen() {
  const { caseId, role, caseView, notFound, loadError } = useCase();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  // Saves run one after another so the server always ends with the latest selection.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const pending = useRef(0);
  const triageGranted = caseView?.consent.triage === "granted";

  useEffect(() => {
    if (!caseId || !triageGranted) return;
    api
      .get<BodyMapView>(`cases/${caseId}/body-map`)
      .then((v) => setSelected(sanitize(v.regions)))
      .catch((err) => {
        setSaveError(explainError(err, "Could not load the saved body map. Check the connection and reload."));
        setSelected([]);
      });
  }, [caseId, triageGranted]);

  if (notFound) return <CaseNotFound />;
  const triage = caseView?.consent.triage ?? null;

  function change(next: string[]) {
    if (!caseId) return;
    setSelected(next);
    setSaveError(null);
    setSaveState("saving");
    pending.current += 1;
    queue.current = queue.current.then(async () => {
      try {
        await api.put<BodyMapView>(`cases/${caseId}/body-map`, { regions: next });
      } catch (err) {
        setSaveError(explainError(err, "The body map was not saved. Check the connection and choose an area again."));
      } finally {
        pending.current -= 1;
        if (pending.current === 0) setSaveState("saved");
      }
    });
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
          <Notice tone="info" title="Saved with this case">
            <p>
              These areas are what the patient points to, not a diagnosis. They are saved with the case so the health worker and doctor can see
              them. They are never used to work out urgency.
            </p>
          </Notice>
          {saveError && (
            <Notice tone="error" role="alert">
              {saveError}
            </Notice>
          )}
          <BodyMap selected={selected} onChange={change} />
          <p className="text-sm text-muted" role="status" aria-live="polite">
            {saveState === "saving" ? "Saving…" : saveState === "saved" && !saveError ? "Saved." : ""}
          </p>
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
