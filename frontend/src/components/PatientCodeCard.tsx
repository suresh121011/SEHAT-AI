"use client";

// SEHAT patient code (P1 patient registry; backend app/patient_registry.py). A visit is linked to a patient ONLY by a
// code the patient holds, typed by a health worker who confirms it — no name, no ID number, nothing matched
// automatically. The ANM gives a new code or links by a shown code; the ANM or a medical officer can undo a wrong link.
// Linked visits show dates and facility only: clinical content stays behind each visit's own screens and checks.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button, Card, Notice, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { BASIS_WORDS, explainPatientError, formatTyped, looksComplete, type Lookup, type PatientLink } from "@/lib/patientCode";

export function PatientCodeCard({ caseId, canIssue, visitHref }: {
  caseId: string;
  /** The visit's ANM: may give a new code or link by a shown code. Others only see the link (and may undo it). */
  canIssue: boolean;
  /** Where a linked visit opens for this role, or null to list it without a link. */
  visitHref: ((caseId: string) => string) | null;
}) {
  const [link, setLink] = useState<PatientLink | null>(null);
  const [typed, setTyped] = useState("");
  const [found, setFound] = useState<Lookup | null>(null);
  const [issued, setIssued] = useState<string | null>(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLink(await api.get<PatientLink>(`cases/${caseId}/patient`));
    } catch (err) {
      setMessage(explainPatientError(err));
    }
  }, [caseId]);
  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
    } catch (err) {
      setMessage(explainPatientError(err));
    } finally {
      setBusy(false);
      await load();
    }
  }

  if (!link) return message ? <Notice tone="error" role="alert">{message}</Notice> : null;
  const consented = link.link_visits_consent === "granted";

  return (
    <Card as="section" className="space-y-3">
      <h2 className="text-xl font-bold">SEHAT patient code</h2>
      {message && <Notice tone="error" role="alert">{message}</Notice>}

      {link.linked && link.patient_code ? (
        <>
          <p className="text-sm">
            This visit is linked to patient code <strong className="font-mono text-lg">{link.patient_code}</strong>
            {link.basis && <span className="text-muted"> ({BASIS_WORDS[link.basis] ?? link.basis})</span>}.
          </p>
          {issued && (
            <Notice tone="success" role="status">
              <p>Write <strong className="font-mono">{issued}</strong> on a slip for the patient. Showing it at a later visit links that visit to this one.</p>
            </Notice>
          )}
          {link.other_visits_hidden_reason ? (
            <p className="text-sm text-muted">Other visits are not shown: the patient has not agreed (or withdrew) linking for this visit.</p>
          ) : link.other_visits.length === 0 ? (
            <p className="text-sm text-muted">No other linked visits that you can see.</p>
          ) : (
            <div className="space-y-1">
              <p className="text-sm font-bold">Other linked visits you can see</p>
              <ul className="space-y-0.5 text-sm">
                {link.other_visits.map((v) => (
                  <li key={v.case_id}>
                    {visitHref ? <Link href={visitHref(v.case_id)} className="text-primary underline">{v.visit_date}</Link> : v.visit_date}
                    <span className="text-muted"> · {v.facility_code} · {v.scenario}</span>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted">Each visit opens with its own consent and access checks. Nothing from another visit changes this visit&apos;s urgency.</p>
            </div>
          )}
          {confirmUnlink ? (
            <div role="alertdialog" aria-label="Unlink this visit" className="space-y-2 rounded-lg border border-error/50 bg-error-bg p-3 text-sm">
              <p className="font-bold">Unlink this visit from patient {link.patient_code}?</p>
              <p>Use this when the code belongs to someone else. The link stays in the record as undone; nothing is deleted.</p>
              <div className="flex flex-wrap gap-2">
                <Button variant="danger" disabled={busy} onClick={() => act(async () => {
                  await api.post(`cases/${caseId}/patient/unlink`, { reason_code: "linked_in_error", confirm: true });
                  setConfirmUnlink(false);
                  setIssued(null);
                })}>Yes, unlink (linked in error)</Button>
                <Button variant="secondary" disabled={busy} onClick={() => setConfirmUnlink(false)}>Keep the link</Button>
              </div>
            </div>
          ) : (
            <Button variant="secondary" disabled={busy} onClick={() => setConfirmUnlink(true)}>This code is not this patient&apos;s — unlink</Button>
          )}
        </>
      ) : !consented ? (
        <p className="text-sm text-muted">
          The patient has not agreed to linking visits, so this visit stays on its own. No code is given and no earlier visit is shown.
        </p>
      ) : !canIssue ? (
        <p className="text-sm text-muted">This visit is not linked to a patient code. The health worker links it at intake.</p>
      ) : (
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-bold">First visit?</p>
            <Button disabled={busy} onClick={() => act(async () => {
              const r = await api.post<{ patient_code: string }>(`cases/${caseId}/patient/new`, {});
              setIssued(r.patient_code);
            })}>Give the patient a new code</Button>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-bold">Returning patient with a code slip?</p>
            <label className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono">SP-</span>
              <input value={typed} onChange={(e) => { setTyped(formatTyped(e.target.value)); setFound(null); }} placeholder="XXXX-XXXX" autoComplete="off"
                aria-label="Patient code from the slip (8 characters after SP-)" className={`${inputClass} w-40 font-mono uppercase`} />
              <Button variant="secondary" disabled={busy || !looksComplete(typed)} onClick={() => act(async () => {
                setFound(await api.post<Lookup>(`cases/${caseId}/patient/lookup`, { patient_code: typed }));
              })}>Check code</Button>
            </label>
            {found && (
              <div className="space-y-2 rounded-lg border border-primary/40 p-3 text-sm">
                <p>
                  Patient <strong className="font-mono">{found.patient_code}</strong>: {found.visible_visits} earlier visit{found.visible_visits === 1 ? "" : "s"} you can see
                  {found.first_visit_date ? `, first on ${found.first_visit_date}` : ""}.
                </p>
                <p className="text-xs text-muted">Check the code on the patient&apos;s own slip before confirming. Linking shows those visits to the staff treating this patient.</p>
                <Button disabled={busy} onClick={() => act(async () => {
                  await api.post(`cases/${caseId}/patient/link`, { patient_code: found.patient_code, confirm: true });
                  setFound(null);
                  setTyped("");
                })}>Yes, the slip matches — link this visit</Button>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
