"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { CaseNotFound, IntakeShell } from "@/components/IntakeShell";
import { Card, Notice, buttonClass } from "@/components/ui";
import { type DocType, DOC_TYPE_LABEL, DocumentUpload } from "@/components/DocumentUpload";
import { CheckFirstPanel } from "@/components/review/CheckFirstPanel";
import { DocumentChecks, ENGINE_LABEL, explain } from "@/components/review/DocumentChecks";
import { MedGemmaFindings } from "@/components/MedGemmaFindings";
import { MedicalImageUpload } from "@/components/MedicalImageUpload";
import { ApiError, api } from "@/lib/api";
import type { Doc, Reference } from "@/lib/documentReview";
import { RANGE_TEXT } from "@/lib/evidence";
import {
  IMAGE_TYPES,
  aiAvailabilityNote,
  imageTypeAvailability,
  type ImageCaps,
  type ImageType,
  type MedicalImageItem,
  type MedicalImagesResp,
} from "@/lib/medicalImages";
import { retakeAdvice } from "@/lib/retake";
import { useDocumentVerification } from "@/lib/useDocumentVerification";

// MedGemma keys (medgemma_*, supported_image_types) come from ImageCaps and are optional: an older backend omits them.
type Caps = ImageCaps & { max_bytes: number; retention_days: number | null; engines: Record<string, { enabled: boolean; ready: boolean }> };
type Category = "text" | "image";
type CaseView = { patient_token: string; is_creator: boolean; is_handler?: boolean; consent: { triage: string } };
type Reviewed = { values: { field_id: string; name: string; name_raw: string; outcome: string; value: Record<string, unknown>; ranges: { printed_range_status: string; reference: Reference } | null }[]; unresolved: unknown[]; note: string };

function DocumentsScreen() {
  const params = useSearchParams();
  const caseId = params.get("case");
  // Deep links from the encounter summary (H1-3): `field` opens that row for checking, `doc` goes to its patient question.
  const focus = { field: params.get("field"), doc: params.get("doc") };
  const [role, setRole] = useState<string | null>(null);
  const [caps, setCaps] = useState<Caps | null>(null);
  const [caseView, setCaseView] = useState<CaseView | null>(null);
  const [reviewed, setReviewed] = useState<Reviewed | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [category, setCategory] = useState<Category>("text");
  const [images, setImages] = useState<MedicalImageItem[]>([]);
  const [lastImage, setLastImage] = useState<MedicalImageItem | null>(null);
  const reviewer = role === "anm" || role === "medical_officer";
  // Documents, the checking order and the open row: the same hook the medical officer's verification page uses.
  const v = useDocumentVerification(caseId, reviewer, caseView?.consent.triage === "granted", focus);
  const { docs, queue, selected } = v;

  const loadCase = useCallback(async () => {
    if (!caseId) return;
    try {
      const cv = await api.get<CaseView>(`cases/${caseId}`);
      setCaseView(cv);
      if (cv.consent.triage === "granted") {
        if (reviewer) setReviewed(await api.get<Reviewed>(`cases/${caseId}/documents/reviewed`));
        // Medical images: an older backend or a disabled feature answers 404/503; then there is simply nothing to list.
        const imgs = await api.get<MedicalImagesResp>(`cases/${caseId}/medical-images`).catch(() => null);
        setImages(imgs?.medical_images ? [...imgs.medical_images].reverse() : []);
      } else {
        setReviewed(null);
        setImages([]);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && err.code !== "FEATURE_DISABLED") setNotFound(true);
    }
  }, [caseId, reviewer]);
  const refresh = useCallback(async () => {
    await loadCase();
    await v.refresh();
  }, [loadCase, v.refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api.get<{ role: string }>("auth/me").then((me) => setRole(me.role)).catch(() => setMessage("Could not check your session. Please log in again."));
    api.get<Caps>("intake/document/capabilities").then(setCaps).catch(() => setMessage("Could not load document settings from the server."));
  }, []);
  useEffect(() => {
    void loadCase();
  }, [loadCase]);
  // The decided row's "Reviewed values" entry updates too.
  const afterDecision = useCallback(async (decidedFieldId: string | null) => {
    await loadCase();
    await v.afterDecision(decidedFieldId);
  }, [loadCase, v.afterDecision]); // eslint-disable-line react-hooks/exhaustive-deps

  // One upload path for text documents and medical images: same form fields, idempotency key and error handling.
  async function send<T>(file: File, type: DocType | ImageType, signal: AbortSignal, extra: Record<string, string> = {}): Promise<T | null> {
    if (!caseId) return null;
    setMessage(null);
    const form = new FormData();
    form.set("case_id", caseId);
    form.set("document_type", type);
    form.set("idempotency_key", crypto.randomUUID());
    for (const [k, v] of Object.entries(extra)) form.set(k, v);
    form.set("file", file);
    let out: T | null = null;
    try {
      out = await api.postForm<T>("intake/document", form, signal);
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) setMessage(explain(err));
    }
    await refresh();
    return out;
  }

  async function upload(file: File, type: DocType, signal: AbortSignal) {
    const doc = await send<Doc>(file, type, signal);
    if (doc) v.setSelected(doc.fields[0] ? { doc: doc.document_id, field: doc.fields[0].field_id } : null);
  }

  async function uploadImage(file: File, type: ImageType, synthetic: boolean | null, signal: AbortSignal): Promise<boolean> {
    const item = await send<MedicalImageItem>(file, type, signal, synthetic === null ? {} : { synthetic_attestation: synthetic ? "true" : "false" });
    if (item) setLastImage(item);
    return !!item;
  }

  const imageAvailability = useMemo(
    () => Object.fromEntries(IMAGE_TYPES.map((t) => [t.key, imageTypeAvailability(caps, t.key)])) as Record<ImageType, { ok: boolean; reason: string | null }>,
    [caps],
  );

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

  if (!caseId || notFound) return <CaseNotFound />;

  const triageOk = caseView?.consent.triage === "granted";
  // A patient uploads text documents to a case its own account started; review stays with the health worker or doctor.
  const isPatient = role === "patient";
  const canUpload = role === "anm" ? !!(caseView?.is_creator || caseView?.is_handler) : isPatient && !!caseView?.is_creator;
  const shownCategory: Category = isPatient ? "text" : category;

  return (
    <IntakeShell
      wide
      step="documents"
      caseId={caseId}
      role={role}
      triage={(caseView?.consent.triage ?? null) as "not_provided" | "granted" | "declined" | "withdrawn" | null}
      token={caseView?.patient_token}
      intro={
        isPatient
          ? "Upload a photo or scan of your lab report, prescription or discharge summary. A health worker checks every value with you before it is used."
          : "Upload a photo or scan of a lab report or prescription, or a medical image. The health worker checks every value against the paper."
      }
    >
      {!isPatient && <fieldset className="space-y-2">
        <legend className="font-bold">Upload category</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["text", "document", "Text documents", "Lab reports and prescriptions: values are read on this server for you to check."],
              ["image", "xray", "Medical images", "X-ray, ECG, CT scans and wounds: AI-described findings, never a diagnosis."],
            ] as const
          ).map(([k, icon, title, text]) => {
            const on = category === k;
            return (
              <button
                key={k}
                type="button"
                aria-pressed={on}
                onClick={() => setCategory(k)}
                className={`flex min-h-11 items-start gap-3 rounded-[16px] border border-white/60 p-4 text-left transition-all duration-300 ${on ? "bg-surface-1 shadow-[inset_2px_2px_5px_rgba(0,0,0,0.05),inset_-2px_-2px_5px_rgba(255,255,255,0.7)] ring-2 ring-primary border-primary" : "bg-card shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)] hover:shadow-[6px_6px_15px_0px_rgba(0,0,0,0.05),-6px_-6px_15px_0px_rgba(255,255,255,0.9)] hover:-translate-y-1"}`}
              >
                <span className={`inline-flex size-10 shrink-0 items-center justify-center rounded-lg ${on ? "bg-primary text-white" : "bg-primary-tint text-primary"}`}>
                  <Icon name={icon} size={20} />
                </span>
                <span className="min-w-0">
                  <span className="block font-bold text-ink">{title}</span>
                  <span className="block text-sm leading-5 text-muted">{text}</span>
                </span>
                {on && <Icon name="check" size={18} className="ml-auto mt-0.5 text-primary" label="Selected" />}
              </button>
            );
          })}
        </div>
      </fieldset>}
      {shownCategory === "image" && (
        <Notice tone="warning" className="space-y-1">
          <p>
            <strong>AI-described visual findings — not a diagnosis.</strong> A doctor reviews the original image. Image findings <strong>never change triage urgency</strong>;
            a matched keyword only flags the case for the reviewer.
          </p>
          {aiAvailabilityNote(caps) && <p className="text-sm">{aiAvailabilityNote(caps)}</p>}
        </Notice>
      )}
      {isPatient && (
        <Notice tone="info" title="What happens to your report">
          <p>
            The report is read on this server only. The numbers it finds are <strong>not checked yet</strong> and are not used for anything until a
            health worker checks them with you against the paper. They never change how urgent your case is. X-rays and other medical images are
            uploaded by the health worker.
          </p>
        </Notice>
      )}
      {!isPatient && shownCategory === "text" && <Notice tone="info">
        <p>
          Machine-read and auto-checked — <strong>not yet confirmed by you</strong>. Reviewed values are a checked record shown alongside the case. They
          <strong> never change triage or urgency</strong>; a person enters any value on the triage form, which keeps a human responsible. Documents are read on this
          server only.
        </p>
      </Notice>}
      {caps && !caps.ocr_enabled && <p role="note">Document reading is turned off on this server.</p>}
      {caseView && !triageOk && (
        <p role="alert">
          Triage consent is not in effect for this case.{" "}
          <Link href={`/intake/consent?case=${caseId}`} className="text-primary underline">Go to consent</Link>
        </p>
      )}
      {caps?.ocr_enabled && (
        <p className="text-xs text-muted">
          Stored page images and read text are {caps.retention_days ? `deleted automatically after ${caps.retention_days} days` : "kept until a health worker deletes them"} (setting chosen by
          the organisation running this system). Copies of the database file (backups) are not covered.
        </p>
      )}
      {shownCategory === "text" && triageOk && caps?.ocr_enabled && canUpload && (
        <DocumentUpload maxBytes={caps.max_bytes} available={caps.document_types} onUpload={upload} />
      )}
      {shownCategory === "image" && triageOk && caps?.ocr_enabled && canUpload && (
        <MedicalImageUpload maxBytes={caps.max_bytes} availability={imageAvailability} cloud={!!caps.medgemma_cloud} onUpload={uploadImage} />
      )}
      {triageOk && !canUpload && role === "patient" && <p className="text-sm">Please hand the report to the health worker to upload.</p>}
      {message && <p role="alert" className="text-sm text-error">{message}</p>}
      {shownCategory === "text" && reviewer && triageOk && (
        <CheckFirstPanel caseId={caseId} queue={queue} selectedFieldId={current?.f?.field_id ?? null} onOpen={v.openQueueItem} />
      )}

      {shownCategory === "image" && triageOk && (
        <section aria-label="Medical images for this case" className="space-y-3">
          {lastImage && <MedGemmaFindings findings={images.find((i) => i.document_id === lastImage.document_id) ?? lastImage} caseId={caseId} />}
          {images.filter((i) => i.document_id !== lastImage?.document_id).length > 0 && (
            <>
              <h2 className="font-bold">Medical images uploaded earlier for this case</h2>
              {images
                .filter((i) => i.document_id !== lastImage?.document_id)
                .map((i) => (
                  <MedGemmaFindings key={i.document_id} findings={i} caseId={caseId} />
                ))}
            </>
          )}
          {!lastImage && images.length === 0 && <p className="text-sm text-muted">No medical images uploaded for this case yet.</p>}
        </section>
      )}

      {shownCategory === "text" && docs.map((d) => {
        const open = d.fields.filter((f) => f.review_status === "machine_read").length; // the patient's status line
        return (
          <Card as="article" key={d.document_id} className="space-y-3">
            {isPatient ? (
              <p className="text-sm">
                <strong>{DOC_TYPE_LABEL[d.document_type]}</strong>
                <span className="text-muted"> · uploaded {new Date(d.created_at).toLocaleTimeString()}</span>
              </p>
            ) : <p className="text-sm">
              <strong>{DOC_TYPE_LABEL[d.document_type]}</strong> · read by {Object.entries(d.engines).map(([e, s]) => `${ENGINE_LABEL[e] ?? e}: ${s === "ok" ? "done" : s.replaceAll("_", " ")}`).join(" · ")}
              <span className="text-muted"> · {new Date(d.created_at).toLocaleTimeString()}</span>
            </p>}
            {d.status === "deleted" && (
              <p role="note">
                Deleted ({d.deleted?.reason === "retention_expired" ? "retention period ended" : `by the ${d.deleted?.by_role ?? "reviewer"}`}) — page images, read
                text and decisions were removed from this server (backups are not covered).
              </p>
            )}
            {d.status === "quality_rejected" && (
              <div role="alert" className="space-y-1 rounded-lg border border-error/50 p-3 text-sm">
                <p><strong>Not read — please upload a better copy.</strong> Nothing was extracted.</p>
                <ul className="list-disc pl-5">
                  {retakeAdvice((d.quality ?? []).flatMap((q) => q.reasons)).map((a) => <li key={a}>{a}</li>)}
                </ul>
              </div>
            )}
            {d.status !== "completed" && d.status !== "deleted" && d.status !== "quality_rejected" && !(d.status === "pending" && isPatient) && (
              <p role="alert">This document was not read ({d.status.replaceAll("_", " ")}). Nothing was extracted.</p>
            )}
            {reviewer && d.status !== "deleted" && d.status !== "pending" && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {confirmDelete === d.document_id ? (
                  <>
                    <span role="alert">Delete this document&apos;s page images, read text and all review decisions? This cannot be undone.</span>
                    <button type="button" onClick={() => remove(d)} className={buttonClass("danger")}>Yes, delete</button>
                    <button type="button" onClick={() => setConfirmDelete(null)} className={buttonClass("secondary")}>Keep</button>
                  </>
                ) : (
                  <button type="button" onClick={() => setConfirmDelete(d.document_id)} className={buttonClass("danger")}>
                    Delete this document
                  </button>
                )}
              </div>
            )}
            {d.status === "pending" && isPatient && <p role="status">Processing… the report is still being read.</p>}
            {d.status === "completed" && isPatient && (
              <p role="status" className="text-sm">
                {d.fields.length === 0
                  ? "Uploaded. No values were recognised; the health worker will read the paper with you."
                  : open > 0
                    ? `Uploaded and read (${d.fields.length} ${d.fields.length === 1 ? "value" : "values"} found). Waiting for a health worker to check it with you.`
                    : "Checked by a health worker."}
              </p>
            )}
            {d.status === "completed" && !isPatient && (
              <>
                <DocumentChecks d={d} caseId={caseId} reviewer={reviewer} selected={current?.f ? { doc: current.d.document_id, field: current.f.field_id } : null}
                  onSelect={v.setSelected} onAttest={attest} onDecided={afterDecision} setMessage={setMessage} />
              </>
            )}
          </Card>
        );
      })}

      {shownCategory === "text" && reviewer && reviewed && (
        <Card as="aside" className="space-y-2">
          <h2 className="font-bold">Reviewed values</h2>
          <p className="text-xs">Only rows the health worker or doctor confirmed or corrected appear here.</p>
          {reviewed.values.length === 0 ? (
            <p className="text-sm text-muted">None yet.</p>
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
          <p className="text-xs text-muted">{reviewed.note}</p>
        </Card>
      )}
    </IntakeShell>
  );
}

export default function DocumentsPage() {
  return (
    <Suspense>
      <DocumentsScreen />
    </Suspense>
  );
}
