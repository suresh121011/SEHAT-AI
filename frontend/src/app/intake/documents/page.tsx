"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { type DocType, DOC_TYPE_LABEL, DocumentUpload } from "@/components/DocumentUpload";
import { EvidenceViewer } from "@/components/EvidenceViewer";
import { ApiError, api } from "@/lib/api";
import { instruction, RANGE_TEXT, type Region, REVIEW_TEXT } from "@/lib/evidence";

type Check = { check: string; status: string; reason: string };
type Reading = { engine: string; text: string; score: number | null };
type Reference = { status: string; citation: string; population: string; band: { label: string } | null; note: string } | null;
type Field = {
  field_id: string; kind: "lab" | "medication"; page_index: number; regions: Region[]; regions_sha256: string; page_png_sha256: string;
  readings: Reading[]; checks: Check[]; band: string; field_confidence: number | null; disputed: boolean; can_confirm: boolean;
  review_status: string; review: { event_id: string; outcome: string; actor_role: string } | null;
  reviewed_value: Record<string, unknown> | null;
  reviewed_ranges?: { printed_range_status: string; reference: Reference };
  // lab
  name_raw?: string; analyte_key?: string | null; value?: { raw: string; comparator: string | null }; unit?: { raw: string; key: string | null }; range?: { raw: string };
  flag_raw?: string; printed_range_status?: string; reference?: Reference;
  // medication
  line_raw?: string; drug_raw?: string; strength_raw?: string | null; dosage_pattern?: string | null; frequency_raw?: string | null;
  duration_raw?: string | null; rxnorm?: { status: string; name: string | null; reason: string } | null;
};
type Doc = {
  document_id: string; status: string; document_type: DocType; engines: Record<string, string>; overall_confidence: number | null;
  pages: { page_index: number; width: number; height: number; png_sha256: string }[];
  dates: { collected_date: string | null; report_date: string | null } | null;
  attestation: { event_id: string; answer: string; actor_role: string } | null; fields: Field[]; status_note: string; created_at: string;
  deleted?: { reason: string; at: string; by_role: string };
};
type Caps = { ocr_enabled: boolean; document_types: Record<string, boolean>; max_bytes: number; retention_days: number | null; engines: Record<string, { enabled: boolean; ready: boolean }> };
type CaseView = { patient_token: string; is_creator: boolean; consent: { triage: string } };
type Reviewed = { values: { field_id: string; name: string; name_raw: string; outcome: string; value: Record<string, unknown>; ranges: { printed_range_status: string; reference: Reference } | null }[]; unresolved: unknown[]; note: string };

const ENGINE_LABEL: Record<string, string> = { paddleocr: "PaddleOCR", surya: "Surya OCR 2", chandra: "Chandra OCR 2" };
// Mirrors backend app/ocr/lexicon.UNITS keys (structured corrections only, no free text).
const UNITS = ["g/dL", "mg/dL", "mmol/L", "µmol/L", "%", "/cumm", "lakhs/cumm", "10^3/µL", "10^6/µL", "fL", "pg", "mm/hr", "U/L", "mIU/L", "µIU/mL", "mIU/mL", "ng/mL", "ng/dL", "µg/dL", "pg/mL", "none"];

function explain(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Nothing was saved; you can retry.";
  const reasons = (err.details?.reasons as string[] | undefined) ?? [];
  switch (err.code) {
    case "DOCUMENT_QUALITY_LOW":
      return `The photo is not clear enough to read (${reasons.join(", ").replaceAll("_", " ")}). Please retake it: flat page, good light, in focus.`;
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
      className={`w-full rounded border p-2 text-left text-sm ${selected ? "border-fuchsia-600 ring-2 ring-fuchsia-600" : "border-black/15 dark:border-white/15"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <strong>{name || "(no name read)"}</strong>
        <span className="text-xs">{REVIEW_TEXT[f.review_status] ?? f.review_status}</span>
      </div>
      {f.kind === "lab" ? (
        <div className="font-mono">
          {f.value?.raw || "—"} {f.unit?.raw} <span className="opacity-60">· range {f.range?.raw || "not printed"}{f.flag_raw ? ` · flag ${f.flag_raw}` : ""}</span>
        </div>
      ) : (
        <div className="font-mono">{[f.strength_raw, f.dosage_pattern, f.frequency_raw, f.duration_raw].filter(Boolean).join(" · ") || f.line_raw}</div>
      )}
      <div className="mt-1 text-xs">
        <span aria-hidden="true">{ins.symbol} </span>
        {ins.text}
      </div>
      {f.kind === "lab" && f.printed_range_status && f.printed_range_status !== "range_unavailable" && (
        <div className="text-xs opacity-80">
          {RANGE_TEXT[f.printed_range_status]?.symbol} {RANGE_TEXT[f.printed_range_status]?.text} printed on the report (machine-read, pending review)
        </div>
      )}
    </button>
  );
}

function ReviewPanel({ f, doc, caseId, reviewer, onDone, setMessage }: { f: Field; doc: Doc; caseId: string; reviewer: boolean; onDone: () => Promise<void>; setMessage: (m: string | null) => void }) {
  const [value, setValue] = useState("");
  const [comparator, setComparator] = useState<string>(f.value?.comparator ?? "=");
  const [unit, setUnit] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const attested = doc.attestation?.answer === "matches";
  async function decide(outcome: string, correction?: Record<string, unknown>) {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/documents/fields/${f.field_id}/review`, {
        outcome, correction, supersedes: f.review?.event_id ?? null, shown_png_sha256: f.page_png_sha256, shown_regions_sha256: f.regions_sha256,
      });
    } catch (err) {
      setMessage(explain(err));
    } finally {
      await onDone();
      setBusy(false);
    }
  }
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
  return (
    <div className="space-y-2 rounded border border-black/10 p-3 text-sm dark:border-white/15">
      <h3 className="font-semibold">Readings</h3>
      <ul>
        {f.readings.map((r, i) => (
          <li key={i} className="font-mono">
            {ENGINE_LABEL[r.engine.split("-")[0]] ?? r.engine}: “{r.text}”
          </li>
        ))}
      </ul>
      {f.disputed && <p role="alert">The readings differ. Choose the correct value from the paper — nothing is chosen for you.</p>}
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
            <button type="button" disabled={busy || !attested || !f.can_confirm} onClick={() => decide("confirmed")} className="rounded bg-green-700 px-3 py-1 text-white disabled:opacity-50">
              ✓ Confirm this row (name, value, unit, range)
            </button>
            <button type="button" disabled={busy} onClick={() => decide("unsure")} className="rounded border border-black/20 px-3 py-1 dark:border-white/20">
              ? Not sure — leave unresolved
            </button>
            <button type="button" disabled={busy} onClick={() => decide("rejected")} className="rounded border border-black/20 px-3 py-1 dark:border-white/20">
              ✗ Wrong / not on report
            </button>
          </div>
          {!f.can_confirm && (
            <p className="text-xs">
              Confirm is off for this row:{" "}
              {f.disputed ? "the engines read it differently" : f.band === "human_entry" ? "it could not be read" : "part of the row cannot be shown on the image"}.
              {f.kind === "medication" ? " Reject it and write the medicine on the triage form from the paper." : " Type it from the paper below, mark it not sure, or reject it."}
            </p>
          )}
          {f.kind === "lab" && (
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs">
                Sign{" "}
                <select value={comparator} onChange={(e) => setComparator(e.target.value)} className="rounded border border-black/20 px-1 dark:border-white/20">
                  {["=", "<", "<=", ">", ">="].map((c) => (
                    <option key={c} value={c}>{c === "=" ? "(none)" : c}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs">
                Result from the paper (was “{f.value?.raw || "—"}”):{" "}
                <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className="w-28 rounded border border-black/20 px-2 py-0.5 font-mono dark:border-white/20" />
              </label>
              <label className="text-xs">
                Unit (was “{f.unit?.raw || "—"}”){" "}
                <select value={unit} onChange={(e) => setUnit(e.target.value)} className="rounded border border-black/20 px-1 dark:border-white/20">
                  <option value="">keep</option>
                  {UNITS.map((u) => (
                    <option key={u} value={u}>{u === "none" ? "no unit printed" : u}</option>
                  ))}
                </select>
              </label>
              <button type="button" disabled={busy || !attested} onClick={correctValue} className="rounded border border-blue-600 px-3 py-1 text-blue-700 disabled:opacity-50 dark:text-blue-300">
                ✎ Save correction
              </button>
            </div>
          )}
          <p className="text-xs opacity-70">Wrong test or drug name? Reject the row. Your decision is recorded with your role; the machine reading is kept unchanged.</p>
        </div>
      ) : (
        <p className="text-xs">The health worker who created this case reviews these values (a doctor view comes in a later phase).</p>
      )}
    </div>
  );
}

function DocumentsScreen() {
  const caseId = useSearchParams().get("case");
  const [role, setRole] = useState<string | null>(null);
  const [caps, setCaps] = useState<Caps | null>(null);
  const [caseView, setCaseView] = useState<CaseView | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [reviewed, setReviewed] = useState<Reviewed | null>(null);
  const [selected, setSelected] = useState<{ doc: string; field: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const reviewer = role === "anm" || role === "medical_officer";

  const refresh = useCallback(async () => {
    if (!caseId) return;
    try {
      const cv = await api.get<CaseView>(`cases/${caseId}`);
      setCaseView(cv);
      if (cv.consent.triage === "granted") {
        setDocs((await api.get<{ documents: Doc[] }>(`cases/${caseId}/documents`)).documents.reverse());
        if (reviewer) setReviewed(await api.get<Reviewed>(`cases/${caseId}/documents/reviewed`));
      } else {
        setDocs([]);
        setReviewed(null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && err.code !== "FEATURE_DISABLED") setNotFound(true);
    }
  }, [caseId, reviewer]);

  useEffect(() => {
    api.get<{ role: string }>("auth/me").then((me) => setRole(me.role)).catch(() => setMessage("Could not check your session. Please log in again."));
    api.get<Caps>("intake/document/capabilities").then(setCaps).catch(() => setMessage("Could not load document settings from the server."));
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  async function upload(file: File, type: DocType, signal: AbortSignal) {
    if (!caseId) return;
    setMessage(null);
    const form = new FormData();
    form.set("case_id", caseId);
    form.set("document_type", type);
    form.set("idempotency_key", crypto.randomUUID());
    form.set("file", file);
    try {
      const doc = await api.postForm<Doc>("intake/document", form, signal);
      setSelected(doc.fields[0] ? { doc: doc.document_id, field: doc.fields[0].field_id } : null);
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) setMessage(explain(err));
    }
    await refresh();
  }

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  async function remove(doc: Doc) {
    setMessage(null);
    try {
      await api.delete(`cases/${caseId}/documents/${doc.document_id}`);
    } catch (err) {
      setMessage(explain(err));
    }
    setConfirmDelete(null);
    await refresh();
  }

  async function attest(doc: Doc, answer: string) {
    setMessage(null);
    try {
      await api.post(`cases/${caseId}/documents/${doc.document_id}/attestation`, { answer, supersedes: doc.attestation?.event_id ?? null });
    } catch (err) {
      setMessage(explain(err));
    }
    await refresh();
  }

  const current = useMemo(() => {
    const d = docs.find((x) => x.document_id === selected?.doc) ?? docs[0];
    const f = d?.fields.find((x) => x.field_id === selected?.field) ?? d?.fields[0];
    return d && f ? { d, f } : d ? { d, f: undefined } : null;
  }, [docs, selected]);

  if (!caseId || notFound) {
    return (
      <section className="mx-auto max-w-xl space-y-4">
        <DemoBanner />
        <p role="alert">Case not found.</p>
        <Link href="/intake" className="text-blue-600 underline">Start a new case</Link>
      </section>
    );
  }

  const triageOk = caseView?.consent.triage === "granted";
  const canUpload = !!caseView?.is_creator && role === "anm";

  return (
    <section className="mx-auto max-w-5xl space-y-5 px-4">
      <DemoBanner />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Lab reports and prescriptions</h1>
        <span className="font-mono text-xs opacity-70">Case {caseView?.patient_token ?? "…"}</span>
      </div>
      <p className="rounded border border-blue-600/40 px-3 py-2 text-sm">
        Machine-read and auto-checked — <strong>not yet confirmed by you</strong>. Reviewed values are a checked record shown alongside the case. They
        <strong> never change triage or urgency</strong>; a person enters any value on the triage form, which keeps a human responsible. Documents are read on this
        server only.
      </p>
      {caps && !caps.ocr_enabled && <p role="note">Document reading is turned off on this server.</p>}
      {caseView && !triageOk && (
        <p role="alert">
          Triage consent is not in effect for this case.{" "}
          <Link href={`/intake/consent?case=${caseId}`} className="text-blue-600 underline">Go to consent</Link>
        </p>
      )}
      {caps?.ocr_enabled && (
        <p className="text-xs opacity-80">
          Stored page images and read text are {caps.retention_days ? `deleted automatically after ${caps.retention_days} days` : "kept until a health worker deletes them"} (setting chosen by
          the organisation running this system). Copies of the database file (backups) are not covered.
        </p>
      )}
      {triageOk && caps?.ocr_enabled && canUpload && (
        <DocumentUpload maxBytes={caps.max_bytes} available={caps.document_types} onUpload={upload} />
      )}
      {triageOk && !canUpload && role === "patient" && <p className="text-sm">Please hand the report to the health worker to upload.</p>}
      {message && <p role="alert" className="text-sm text-red-600">{message}</p>}

      {docs.map((d) => {
        const count = (st: string[]) => d.fields.filter((f) => st.includes(f.review_status)).length;
        const done = count(["confirmed", "corrected"]);
        const unsure = count(["unsure"]);
        const rejected = count(["rejected"]);
        const open = count(["machine_read"]);
        return (
          <article key={d.document_id} className="space-y-3 rounded border border-black/10 p-4 dark:border-white/15">
            <p className="text-sm">
              <strong>{DOC_TYPE_LABEL[d.document_type]}</strong> · read by {Object.entries(d.engines).map(([e, s]) => `${ENGINE_LABEL[e] ?? e}: ${s === "ok" ? "done" : s.replaceAll("_", " ")}`).join(" · ")}
              <span className="opacity-60"> · {new Date(d.created_at).toLocaleTimeString()}</span>
            </p>
            {d.status === "deleted" && (
              <p role="note">
                Deleted ({d.deleted?.reason === "retention_expired" ? "retention period ended" : `by the ${d.deleted?.by_role ?? "reviewer"}`}) — page images, read
                text and decisions were removed from this server.
              </p>
            )}
            {d.status !== "completed" && d.status !== "deleted" && <p role="alert">This document was not read ({d.status.replaceAll("_", " ")}). Nothing was extracted.</p>}
            {reviewer && d.status !== "deleted" && d.status !== "pending" && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {confirmDelete === d.document_id ? (
                  <>
                    <span role="alert">Delete this document&apos;s page images, read text and all review decisions? This cannot be undone.</span>
                    <button type="button" onClick={() => remove(d)} className="rounded bg-red-700 px-3 py-1 text-white">Yes, delete</button>
                    <button type="button" onClick={() => setConfirmDelete(null)} className="rounded border border-black/20 px-3 py-1 dark:border-white/20">Keep</button>
                  </>
                ) : (
                  <button type="button" onClick={() => setConfirmDelete(d.document_id)} className="rounded border border-red-700 px-3 py-1 text-red-700 dark:text-red-300">
                    Delete this document
                  </button>
                )}
              </div>
            )}
            {d.status === "completed" && (
              <>
                <fieldset className="space-y-2 rounded border border-amber-500 p-3">
                  <legend className="px-1 font-semibold">Is this report for the patient in front of you, for this visit?</legend>
                  <p className="text-sm">
                    Check the name and date on the paper. Dates read: collected {d.dates?.collected_date ?? "not found"}, reported {d.dates?.report_date ?? "not found"}.
                  </p>
                  {reviewer ? (
                    <div className="flex flex-wrap gap-2">
                      {[["matches", "Yes, this patient's report"], ["does_not_match", "No"], ["unsure", "Not sure"]].map(([a, t]) => (
                        <button key={a} type="button" aria-pressed={d.attestation?.answer === a} onClick={() => attest(d, a)}
                          className={`rounded border px-3 py-1 text-sm ${d.attestation?.answer === a ? "border-blue-600 bg-blue-600 text-white" : "border-black/20 dark:border-white/20"}`}>
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
                      <li key={f.field_id}>
                        <FieldRow f={f} selected={current?.f?.field_id === f.field_id} onSelect={() => setSelected({ doc: d.document_id, field: f.field_id })} />
                      </li>
                    ))}
                    {d.fields.length === 0 && <li className="text-sm">No values were recognised on this document. Enter them on the triage form from the paper.</li>}
                  </ul>
                  {current && current.d.document_id === d.document_id && current.f && (
                    <div className="space-y-3 md:sticky md:top-4 md:self-start">
                      {(() => {
                        const f = current.f;
                        const pg = d.pages.find((p) => p.page_index === f.page_index) ?? d.pages[0];
                        return pg ? (
                          <EvidenceViewer imageUrl={`/api/backend/cases/${caseId}/documents/${d.document_id}/pages/${pg.page_index}/image`} page={pg} regions={f.regions}
                            label={(f.kind === "lab" ? f.name_raw : f.drug_raw) ?? "value"} />
                        ) : null;
                      })()}
                      <ReviewPanel f={current.f} doc={d} caseId={caseId} reviewer={reviewer} onDone={refresh} setMessage={setMessage} />
                    </div>
                  )}
                </div>
              </>
            )}
          </article>
        );
      })}

      {reviewer && reviewed && (
        <aside className="space-y-2 rounded border border-green-700/50 p-4">
          <h2 className="font-semibold">Reviewed values</h2>
          <p className="text-xs">Only rows you confirmed or corrected appear here.</p>
          {reviewed.values.length === 0 ? (
            <p className="text-sm opacity-80">None yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {reviewed.values.map((v) => {
                const r = v.ranges?.printed_range_status;
                return (
                  <li key={v.field_id}>
                    {v.name_raw}: <strong className="font-mono">{v.value.comparator ? String(v.value.comparator) : ""}{String(v.value.value ?? v.value.qualitative ?? v.value.drug_raw ?? "")} {String(v.value.unit ?? v.value.strength_raw ?? "")}</strong>{" "}
                    <span className="text-xs">({v.outcome})</span>
                    {r && (
                      <span className="ml-2 text-xs">
                        {RANGE_TEXT[r]?.symbol} {RANGE_TEXT[r]?.text} printed by the lab — may not fit this patient&apos;s age/sex; not a diagnosis
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <p className="text-xs opacity-70">{reviewed.note}</p>
        </aside>
      )}
    </section>
  );
}

export default function DocumentsPage() {
  return (
    <Suspense>
      <DocumentsScreen />
    </Suspense>
  );
}
