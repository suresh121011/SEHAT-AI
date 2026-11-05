"use client";

// Checking one document's machine-read values against the page image: "is this the patient's report?", then each row
// (confirm, correct, not sure, reject; keys C/U/R). ONE review experience, used by the ANM's documents step and the
// medical officer's verification page; both call the same backend endpoints (app/routes/ocr.py), which enforce case
// access, facility scope, consent and audit. Moved unchanged from app/intake/documents/page.tsx.
import { useEffect, useState } from "react";

import { EvidenceViewer } from "@/components/EvidenceViewer";
import { Icon } from "@/components/Icon";
import { Card, buttonClass, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { Doc, Field } from "@/lib/documentReview";
import { instruction, RANGE_TEXT, REVIEW_TEXT } from "@/lib/evidence";
import { DURATION_UNITS, FREQUENCIES, buildMedCorrection, medLineText, type MedInput } from "@/lib/medCorrection";
import { retakeAdvice } from "@/lib/retake";
import { keyAction } from "@/lib/verificationQueue";

export const ENGINE_LABEL: Record<string, string> = { paddleocr: "PaddleOCR", surya: "Surya OCR 2", chandra: "Chandra OCR 2" };
// Mirrors backend app/ocr/lexicon.UNITS keys (structured corrections only, no free text).
const UNITS = ["g/dL", "mg/dL", "mmol/L", "µmol/L", "%", "/cumm", "lakhs/cumm", "10^3/µL", "10^6/µL", "fL", "pg", "mm/hr", "U/L", "mIU/L", "µIU/mL", "mIU/mL", "ng/mL", "ng/dL", "µg/dL", "pg/mL", "none"];

export function explain(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Nothing was saved; you can retry.";
  const reasons = (err.details?.reasons as string[] | undefined) ?? [];
  switch (err.code) {
    case "DOCUMENT_QUALITY_LOW":
      return `This document was not read. ${retakeAdvice(reasons).join(" ")}`;
    case "CONSENT_NOTICE_UPDATE_REQUIRED":
      return "The consent notice now explains document upload. Please read it to the patient and record consent again before uploading.";
    case "CONSENT_REQUIRED":
      return "Triage consent is not in effect for this case.";
    case "OCR_BUSY":
      return "Document reading is busy — try again in a minute.";
    case "OCR_TIMEOUT":
      return "Reading took too long and was stopped. Try a clearer photo or fewer pages.";
    case "DOCUMENT_INVALID":
      return "This file could not be opened as a PNG, JPEG or PDF.";
    case "DOCUMENT_TOO_LARGE":
      return "The file is too large.";
    case "ATTESTATION_REQUIRED":
      return "First answer whether this report is the patient's.";
    case "CORRECTION_REQUIRED":
      return "This value can't be confirmed as read. Type it from the paper, mark it not sure, or reject it.";
    case "STALE_DECISION":
      return "Someone changed this in the meantime. The screen has been reloaded — check again.";
    default:
      return err.message;
  }
}

function FieldRow({ f, selected, onSelect }: { f: Field; selected: boolean; onSelect: () => void }) {
  const ins = instruction(f);
  const name = f.kind === "lab" ? f.name_raw : f.drug_raw;
  return (
    <button type="button" onClick={onSelect} aria-pressed={selected}
      className={`w-full rounded-[12px] border border-white/60 p-3 text-left text-sm shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02),inset_-1px_-1px_3px_rgba(255,255,255,0.7)] ${selected ? "bg-surface-1 ring-2 ring-primary border-primary" : "bg-card hover:shadow-[4px_4px_10px_rgba(0,0,0,0.05)] transition-shadow"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <strong>{name || "(no name read)"}</strong>
        <span className="text-xs">{REVIEW_TEXT[f.review_status] ?? f.review_status}</span>
      </div>
      {f.kind === "lab" ? (
        <div className="font-mono">
          {f.value?.raw || "—"} {f.unit?.raw} <span className="text-muted">· range {f.range?.raw || "not printed"}{f.flag_raw ? ` · flag ${f.flag_raw}` : ""}</span>
        </div>
      ) : (
        <div className={`font-mono ${f.strength_status === "readings_differ" ? "text-warning font-bold" : ""}`}>{medLineText(f)}</div>
      )}
      <div className="mt-1 text-xs">
        <span aria-hidden="true">{ins.symbol} </span>
        {ins.text}
      </div>
      {f.kind === "lab" && f.printed_range_status && f.printed_range_status !== "range_unavailable" && (
        <div className="text-xs text-muted">
          {RANGE_TEXT[f.printed_range_status]?.symbol} {RANGE_TEXT[f.printed_range_status]?.text} printed on the report (machine-read, pending review)
        </div>
      )}
    </button>
  );
}

function ReviewPanel({ f, doc, caseId, reviewer, onDone, setMessage }: { f: Field; doc: Doc; caseId: string; reviewer: boolean; onDone: (decidedFieldId: string | null) => Promise<void>; setMessage: (m: string | null) => void }) {
  const [value, setValue] = useState("");
  const [comparator, setComparator] = useState<string>(f.value?.comparator ?? "=");
  const [unit, setUnit] = useState<string>("");
  const [med, setMed] = useState<MedInput>({ strength: "", pattern: "", frequency: "", durationNumber: "", durationUnit: "days" });
  const [busy, setBusy] = useState(false);
  const strengthDisputed = f.strength_status === "readings_differ";
  const attested = doc.attestation?.answer === "matches";
  async function decide(outcome: string, correction?: Record<string, unknown>) {
    setBusy(true);
    setMessage(null);
    let ok = false;
    try {
      await api.post(`cases/${caseId}/documents/fields/${f.field_id}/review`, {
        outcome, correction, supersedes: f.review?.event_id ?? null, shown_png_sha256: f.page_png_sha256, shown_regions_sha256: f.regions_sha256,
      });
      ok = true;
    } catch (err) {
      setMessage(explain(err));
    } finally {
      await onDone(ok ? f.field_id : null); // a recorded decision moves on to the next row to check; an error stays here
      setBusy(false);
    }
  }
  // Keys C / U / R (P0-2): the same three decisions as the buttons, with the same rules. Never Enter, never while typing.
  const canConfirmNow = reviewer && attested && f.can_confirm && !busy;
  useEffect(() => {
    if (!reviewer) return;
    const onKey = (e: KeyboardEvent) => {
      const action = keyAction(e.key, (e.target as HTMLElement | null)?.tagName ?? "", canConfirmNow, e.metaKey || e.ctrlKey || e.altKey);
      if (!action || busy) return;
      e.preventDefault();
      decide(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const correctValue = () => {
    const v = value.trim();
    const correction: Record<string, unknown> = {};
    if (v) {
      if (!/^\d{1,7}(\.\d{1,4})?$/.test(v)) return setMessage("Type the number exactly as printed, e.g. 85000 or 11.2 (no commas).");
      correction.result = { value: v, comparator };
    }
    if (unit) correction.unit = unit;
    if (!Object.keys(correction).length) return setMessage("Type the result and/or choose the unit as printed on the paper.");
    decide("corrected", correction);
  };
  const correctMed = () => {
    const out = buildMedCorrection(med, strengthDisputed);
    if ("error" in out) return setMessage(out.error);
    decide("corrected", out.correction);
  };
  return (
    <Card className="space-y-3 text-sm">
      <h3 className="font-bold">Readings</h3>
      <ul>
        {f.readings.map((r, i) => (
          <li key={i} className="font-mono">
            {ENGINE_LABEL[r.engine.split("-")[0]] ?? r.engine}: “{r.text}”
          </li>
        ))}
      </ul>
      {f.disputed && <p role="alert">The readings differ. Choose the correct value from the paper — nothing is chosen for you.</p>}
      {strengthDisputed && (
        <p role="alert" className="font-bold text-warning">
          The two engines read the strength differently (Chandra: “{f.strength_raw}”). Do not use either reading — type the strength from the paper below.
        </p>
      )}
      {f.kind === "lab" && f.reference && (
        <p className="text-xs">
          Reference: {RANGE_TEXT[f.reference.status]?.symbol} {RANGE_TEXT[f.reference.status]?.text} ({f.reference.population}; {f.reference.citation})
          {f.reference.band && ` · ${f.reference.band.label}`}. The lab&apos;s own printed range may differ and may not fit this patient&apos;s age or sex. Not a diagnosis.
        </p>
      )}
      {f.kind === "medication" && f.rxnorm && (
        <p className="text-xs">
          RxNorm (US drug list, offline): {f.rxnorm.status}
          {f.rxnorm.name ? ` — ${f.rxnorm.name}` : ""} ({f.rxnorm.reason.replaceAll("_", " ")}). Indian brand names are often not listed.
        </p>
      )}
      <details className="text-xs">
        <summary>Automatic checks (engine scores are not probabilities that a value is right)</summary>
        <ul>
          {f.checks.map((c, i) => (
            <li key={i}>
              {c.check}: {c.status}
              {c.reason ? ` (${c.reason})` : ""}
            </li>
          ))}
        </ul>
      </details>
      {reviewer ? (
        <div className="space-y-2" role="group" aria-label="Review decision">
          {!attested && <p role="note">Answer the patient question above before confirming values.</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy || !attested || !f.can_confirm} onClick={() => decide("confirmed")} className={buttonClass("success", "flex-1 sm:flex-none")}>
              <Icon name="check" /> Confirm this row (name, value, unit, range)
            </button>
            <button type="button" disabled={busy} onClick={() => decide("unsure")} className={buttonClass("secondary", "flex-1 sm:flex-none")}>
              ? Not sure — leave unresolved
            </button>
            <button type="button" disabled={busy} onClick={() => decide("rejected")} className={buttonClass("secondary", "flex-1 sm:flex-none")}>
              <Icon name="cross" /> Wrong / not on report
            </button>
          </div>
          {!f.can_confirm && (
            <p className="text-xs">
              Confirm is off for this row:{" "}
              {f.disputed ? "the engines read it differently" : f.band === "human_entry" ? "it could not be read" : "part of the row cannot be shown on the image"}.
              {f.kind === "medication"
                ? f.strength_correctable
                  ? " Type the values from the paper below, mark it not sure, or reject it."
                  : " The drug name itself is uncertain: reject it and write the medicine on the triage form from the paper."
                : " Type it from the paper below, mark it not sure, or reject it."}
            </p>
          )}
          {f.kind === "lab" && (
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs">
                Sign{" "}
                <select value={comparator} onChange={(e) => setComparator(e.target.value)} className={`${inputClass} min-h-8 py-1 px-2 w-auto inline-block`}>
                  {["=", "<", "<=", ">", ">="].map((c) => (
                    <option key={c} value={c}>{c === "=" ? "(none)" : c}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs">
                Result from the paper (was “{f.value?.raw || "—"}”):{" "}
                <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className={`${inputClass} w-28 min-h-8 py-1 px-2 font-mono inline-block`} />
              </label>
              <label className="text-xs">
                Unit (was “{f.unit?.raw || "—"}”){" "}
                <select value={unit} onChange={(e) => setUnit(e.target.value)} className={`${inputClass} min-h-8 py-1 px-2 w-auto inline-block`}>
                  <option value="">keep</option>
                  {UNITS.map((u) => (
                    <option key={u} value={u}>{u === "none" ? "no unit printed" : u}</option>
                  ))}
                </select>
              </label>
              <button type="button" disabled={busy || !attested} onClick={correctValue} className={buttonClass("primary", "flex-1 sm:flex-none")}>
                <Icon name="pencil" /> Save correction
              </button>
            </div>
          )}
          {f.kind === "medication" && f.strength_correctable && (
            <div className="space-y-2 rounded-lg border border-subtle p-2" role="group" aria-label="Correct this medicine line from the paper">
              <p className="text-xs font-bold">Type from the paper (leave a box empty to keep the machine reading{strengthDisputed ? "; the strength is required" : ""}):</p>
              <div className="flex flex-wrap items-center gap-2">
                <label className="text-xs">
                  Strength{strengthDisputed ? " (required)" : ""}{" "}
                  <input value={med.strength} onChange={(e) => setMed({ ...med, strength: e.target.value })} placeholder="e.g. 800 mg"
                    className={`${inputClass} w-28 min-h-8 py-1 px-2 font-mono inline-block`} />
                </label>
                <label className="text-xs">
                  Dose pattern{" "}
                  <input value={med.pattern} onChange={(e) => setMed({ ...med, pattern: e.target.value })} placeholder="1-0-1"
                    className={`${inputClass} w-20 min-h-8 py-1 px-2 font-mono inline-block`} />
                </label>
                <label className="text-xs">
                  Frequency{" "}
                  <select value={med.frequency} onChange={(e) => setMed({ ...med, frequency: e.target.value })} className={`${inputClass} min-h-8 py-1 px-2 w-auto inline-block`}>
                    <option value="">keep</option>
                    {FREQUENCIES.map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                </label>
                <label className="text-xs">
                  Duration{" "}
                  <input inputMode="numeric" value={med.durationNumber} onChange={(e) => setMed({ ...med, durationNumber: e.target.value })} placeholder="7"
                    className={`${inputClass} w-14 min-h-8 py-1 px-2 font-mono inline-block`} />
                  <select value={med.durationUnit} onChange={(e) => setMed({ ...med, durationUnit: e.target.value })} className={`${inputClass} ml-1 min-h-8 py-1 px-2 w-auto inline-block`}>
                    {DURATION_UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
                  </select>
                </label>
                <button type="button" disabled={busy || !attested} onClick={correctMed} className={buttonClass("primary", "flex-1 sm:flex-none")}>
                  <Icon name="pencil" /> Save from paper
                </button>
              </div>
            </div>
          )}
          <p className="text-xs text-muted">Wrong test or drug name? Reject the row. Your decision is recorded with your role; the machine reading is kept unchanged.</p>
          <p className="text-xs text-muted">Keys (when not typing in a box): <kbd>C</kbd> confirm · <kbd>U</kbd> not sure · <kbd>R</kbd> wrong / not on report. After each decision the next row to check opens.</p>
        </div>
      ) : (
        <p className="text-xs">The health worker who created this case reviews these values (a doctor view comes in a later phase).</p>
      )}
    </Card>
  );
}


/** The review part of one completed document: the patient question, the row counts, the rows and the open row's panel. */
export function DocumentChecks({ d, caseId, reviewer, selected, onSelect, onAttest, onDecided, setMessage }: {
  d: Doc;
  caseId: string;
  reviewer: boolean;
  selected: { doc: string; field: string } | null;
  onSelect: (s: { doc: string; field: string }) => void;
  onAttest: (d: Doc, answer: string) => void;
  onDecided: (decidedFieldId: string | null) => Promise<void>;
  setMessage: (m: string | null) => void;
}) {
  const count = (st: string[]) => d.fields.filter((f) => st.includes(f.review_status)).length;
  const done = count(["confirmed", "corrected"]);
  const unsure = count(["unsure"]);
  const rejected = count(["rejected"]);
  const open = count(["machine_read"]);
  const current: Field | undefined = selected?.doc === d.document_id ? d.fields.find((f) => f.field_id === selected.field) : undefined;
  return (
    <>
                <fieldset id={`attest-${d.document_id}`} className="space-y-2 rounded-[16px] border border-warning/40 bg-warning-bg/50 p-4 shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02),inset_-1px_-1px_3px_rgba(255,255,255,0.7)]">
                  <legend className="px-1 font-bold text-warning">Is this report for the patient in front of you, for this visit?</legend>
                  <p className="text-sm">
                    Check the name and date on the paper. Dates read: collected {d.dates?.collected_date ?? "not found"}, reported {d.dates?.report_date ?? "not found"}.
                  </p>
                  {reviewer ? (
                    <div className="flex flex-wrap gap-2">
                      {[["matches", "Yes, this patient's report"], ["does_not_match", "No"], ["unsure", "Not sure"]].map(([a, t]) => (
                        <button key={a} type="button" aria-pressed={d.attestation?.answer === a} onClick={() => onAttest(d, a)}
                          className={buttonClass(d.attestation?.answer === a ? "primary" : "secondary", "text-sm")}>
                          {t}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs">The health worker answers this.</p>
                  )}
                  {d.attestation && d.attestation.answer !== "matches" && (
                    <p role="alert" className="text-sm">Values from this report can&apos;t be confirmed. Upload the right report.</p>
                  )}
                </fieldset>
                <p className="text-sm" aria-live="polite">
                  {done} confirmed or corrected · {unsure} not sure · {rejected} rejected · {open} not yet checked
                  {open === 0 && unsure === 0 && d.fields.length > 0 ? " — review complete" : ""}
                </p>
                <div className="grid gap-4 md:grid-cols-2">
                  <ul className="space-y-2" aria-label="Values read from the document">
                    {d.fields.map((f) => (
                      <li key={f.field_id} id={`field-${f.field_id}`}>
                        <FieldRow f={f} selected={current?.field_id === f.field_id} onSelect={() => onSelect({ doc: d.document_id, field: f.field_id })} />
                      </li>
                    ))}
                    {d.fields.length === 0 && <li className="text-sm">No values were recognised on this document. Enter them on the triage form from the paper.</li>}
                  </ul>
                  {current && (
                    <div className="space-y-3 md:sticky md:top-4 md:self-start">
                      {(() => {
                        const f = current;
                        const pg = d.pages.find((p) => p.page_index === f.page_index) ?? d.pages[0];
                        return pg ? (
                          <EvidenceViewer imageUrl={`/api/backend/cases/${caseId}/documents/${d.document_id}/pages/${pg.page_index}/image`} page={pg} regions={f.regions}
                            label={(f.kind === "lab" ? f.name_raw : f.drug_raw) ?? "value"} />
                        ) : null;
                      })()}
                      <ReviewPanel key={current.field_id} f={current} doc={d} caseId={caseId} reviewer={reviewer} onDone={onDecided} setMessage={setMessage} />
                    </div>
                  )}
                </div>
    </>
  );
}
