"use client";

// AI-described visual findings for one medical image (MedGemma pipeline, docs/18). Amber, so it never looks like the
// blue OCR/lab section. These are descriptions of what the image shows, not a diagnosis: urgency keyword hits are
// raise-only flags for the reviewer (the rules engine still sets urgency), the model's confidence is an uncalibrated
// label that never hides content, text withheld by the non-diagnostic filter is never shown, and the disclaimer is
// rendered for every status. The original image is always one click away.
import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import {
  FALLBACK_DISCLAIMER,
  confidenceBand,
  findingsSourceLabel,
  hasRedFlag,
  hasYellowFlag,
  humanizeField,
  imageTypeIcon,
  imageTypeLabel,
  notedSignals,
  raisingSignals,
  safeImageUrl,
  statusCopy,
  visibleFields,
  withheldSummary,
  type ConfidenceBand,
  type MedicalImageItem,
} from "@/lib/medicalImages";

type Props = {
  findings: MedicalImageItem;
  caseId?: string;
  reviewer?: { onAcknowledge: () => Promise<void> };
};

const BAND: Record<ConfidenceBand, { bar: string; text: string; label: string }> = {
  // Not green: high SELF-reported confidence is not evidence of a correct reading. Local MedGemma reported 95 % on a
  // drawing that was not an X-ray and called it normal (docs/18 §11, 2026-10-05).
  high: { bar: "bg-muted", text: "text-ink", label: "High" },
  moderate: { bar: "bg-warning", text: "text-warning", label: "Moderate" },
  low: { bar: "bg-error", text: "text-error", label: "Low" },
};

export function MedGemmaFindings({ findings: f, reviewer }: Props) {
  const [viewing, setViewing] = useState(false);
  const label = imageTypeLabel(f.image_class || f.declared_type);
  const described = f.status === "described";
  const red = hasRedFlag(f.urgency_signals);
  const yellow = hasYellowFlag(f.urgency_signals);
  const raising = raisingSignals(f.urgency_signals);
  const noted = notedSignals(f.urgency_signals);
  const signalsPresent = (f.urgency_signals ?? []).length > 0;
  const fields = visibleFields(f);
  const withheld = withheldSummary(f.withheld);
  const band = confidenceBand(f.confidence, f.confidence_band);
  const status = statusCopy(f);
  const imageUrl = safeImageUrl(f.source_image_url);
  const headingId = `mg-${f.document_id}`;
  const demo = Boolean(f.provenance?.synthetic) || f.backend === "fake";

  return (
    <article aria-labelledby={headingId} className="space-y-3 rounded-lg border-2 border-warning/60 bg-card p-4">
      <header className="space-y-1 border-b border-warning/40 pb-2">
        <h3 id={headingId} className="flex flex-wrap items-center gap-2 text-lg font-bold text-warning">
          <span aria-hidden="true">🩻</span> {demo ? "Demo Image Findings (canned text)" : "AI Image Findings"}
          <span className="inline-flex items-center gap-1 rounded-full border border-warning/60 bg-warning-bg px-2 py-0.5 text-sm font-bold text-ink">
            <Icon name={imageTypeIcon(f.image_class || f.declared_type)} size={16} />
            {label}
          </span>
        </h3>
        <p className={`text-sm font-bold ${demo ? "text-error" : "text-ink"}`}>{findingsSourceLabel(f)}</p>
        <p className="text-xs text-muted">
          {demo ? "Canned demo text" : "Visual description by AI"} ({f.backend || "unknown backend"}
          {f.model ? ` · ${f.model}` : ""}
          {f.prompt_version ? ` · prompt ${f.prompt_version}` : ""}
          {f.guard_version ? ` · filter ${f.guard_version}` : ""}) · uploaded {f.created_at ? new Date(f.created_at).toLocaleString() : "time unknown"}. Not a diagnosis.
        </p>
      </header>

      {red && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border-2 border-error bg-error px-3 py-2 font-bold text-white animate-pulse motion-reduce:animate-none">
          <span aria-hidden="true">⚠️</span>
          <span>{demo ? "Critical imaging signal in the DEMO text — shown to test the reviewer flow, not from a real image reading" : "Critical imaging signal detected — escalated to reviewer"}</span>
        </div>
      )}
      {!red && yellow && (
        <div role="status" className="flex items-start gap-2 rounded-lg border-2 border-warning bg-warning-bg px-3 py-2 font-bold text-warning">
          <Icon name="alert" size={18} className="mt-0.5" />
          <span>{demo ? "Imaging signal in the DEMO text — shown to test the reviewer flow, not from a real image reading" : "Imaging signal for the reviewer to check"}</span>
        </div>
      )}

      {f.classifier_mismatch && (
        <div role="note" className="flex items-start gap-2 rounded-lg border border-warning bg-warning-bg px-3 py-2 text-sm text-ink">
          <Icon name="alert" size={16} className="mt-0.5 text-warning" />
          <span>
            Selected type: <strong>{imageTypeLabel(f.declared_type)}</strong> · filename suggests: <strong>{imageTypeLabel(f.classifier_hint)}</strong> — reviewer must confirm the image type
          </span>
        </div>
      )}

      {!described && status && (
        <div role="status" className="flex items-start gap-2 rounded-lg border border-subtle bg-esc-ack px-3 py-2 text-sm text-ink">
          <Icon name="info" size={16} className="mt-0.5 text-muted" />
          <span>{status}</span>
        </div>
      )}

      {raising.length > 0 && (
        <ul className="space-y-1 text-sm" aria-label={demo ? "Signals matched in the canned demo text (not from this image)" : "Imaging signals for the reviewer"}>
          {demo && <li className="text-xs font-bold text-error">Matched in the canned demo text, not read from this image:</li>}
          {raising.map((s, i) => (
            <li key={`${s.signal}-${i}`} className={`rounded-lg border-l-4 px-2 py-1 ${s.action === "RED_FLAG" ? "border-error bg-error-bg" : "border-warning bg-warning-bg"}`}>
              <strong>{s.signal.replaceAll("_", " ").toLowerCase()}</strong> <span className="text-xs">({s.action === "RED_FLAG" ? "red flag" : "yellow flag"})</span>
              {s.note && <span className="block text-xs">{s.note}</span>}
            </li>
          ))}
        </ul>
      )}
      {noted.length > 0 && (
        <ul className="space-y-0.5 text-xs text-muted" aria-label="Signals noted for the reviewer">
          {noted.map((s, i) => (
            <li key={`${s.signal}-noted-${i}`}>
              {s.signal.replaceAll("_", " ").toLowerCase()}: noted{s.negated ? " (negated)" : ""} — for reviewer
            </li>
          ))}
        </ul>
      )}
      {signalsPresent && !f.keyword_rules_validated && <p className="text-xs text-muted">Urgency keyword rules are not clinician-validated. They only raise attention; the rules engine and the reviewer decide urgency.</p>}

      {described && fields.length > 0 && (
        <section aria-label="Structured findings" className="rounded-lg border border-subtle">
          <dl className="divide-y divide-subtle">
            {fields.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[minmax(8rem,1fr)_2fr] gap-2 px-3 py-1.5 text-sm">
                <dt className="text-muted">{humanizeField(k)}</dt>
                <dd className="font-bold break-words">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {described && !f.withheld?.description && f.raw_description && (
        <details className="text-sm">
          <summary className="cursor-pointer font-bold text-primary">AI description (filtered)</summary>
          <p className="mt-1 whitespace-pre-line rounded bg-warning-bg/50 px-2 py-1">{f.raw_description}</p>
        </details>
      )}
      {withheld && (
        <p className="flex items-start gap-1 text-sm text-ink">
          <Icon name="lock" size={14} className="mt-1" />
          {withheld}
        </p>
      )}

      {described && band && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span className={`font-bold ${BAND[band].text}`}>{BAND[band].label}{typeof f.confidence === "number" ? ` · ${Math.round(f.confidence * 100)}%` : ""}</span>
            <span className="text-xs text-muted">model&apos;s self-reported confidence (uncalibrated)</span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-skeleton" role="img" aria-label={`Model's self-reported confidence: ${BAND[band].label}`}>
            <div className={`h-full ${BAND[band].bar}`} style={{ width: `${Math.max(4, Math.min(100, Math.round((f.confidence ?? (band === "high" ? 0.9 : band === "moderate" ? 0.65 : 0.3)) * 100)))}%` }} />
          </div>
          {!demo && (
            <p className="text-xs font-bold text-ink">
              A “normal” or “nothing found” description never rules anything out. In testing, this kind of model called a drawing that was not an
              X-ray normal, with high confidence. Look at the image.
            </p>
          )}
          {band === "low" && (
            <p className="flex items-start gap-1 text-xs font-bold text-error">
              <Icon name="alert" size={14} className="mt-0.5" /> Low self-reported confidence: check every finding against the original image. Findings are still shown.
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setViewing(true)}
          disabled={!imageUrl}
          className="inline-flex min-h-11 items-center gap-2 rounded-lg border-2 border-primary bg-card px-4 py-2 font-bold text-primary hover:bg-primary-tint disabled:opacity-55"
        >
          <Icon name="eye" size={16} /> View original image
        </button>
        {!imageUrl && <span className="text-xs text-muted">Source unavailable.</span>}
      </div>

      {reviewer && f.requires_acknowledgement && <AckBox findings={f} onAcknowledge={reviewer.onAcknowledge} />}

      <p className="rounded-lg border border-subtle bg-esc-ack px-3 py-2 text-sm text-ink">{f.disclaimer?.trim() || FALLBACK_DISCLAIMER}</p>

      {imageUrl && <ImageLightbox open={viewing} onClose={() => setViewing(false)} url={imageUrl} label={label} />}
    </article>
  );
}

function AckBox({ findings: f, onAcknowledge }: { findings: MedicalImageItem; onAcknowledge: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = f.acknowledged;
  async function change() {
    if (done || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onAcknowledge();
    } catch {
      setError("Could not record that the findings were reviewed. Nothing was saved; try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-1 rounded-lg border border-warning/60 bg-warning-bg/50 px-3 py-2">
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={done} disabled={done || busy} onChange={change} className="mt-1 size-5 accent-primary" />
        <span>
          <strong>Findings reviewed{f.classifier_mismatch ? " and image type confirmed" : ""}</strong>
          <span className="block text-xs text-muted">
            {done ? "Recorded with your role. " : busy ? "Recording… " : ""}I looked at the original image and these AI-described findings. This is not a diagnosis.
          </span>
        </span>
      </label>
      {error && (
        <p role="alert" className="text-xs text-error">
          {error}
        </p>
      )}
    </div>
  );
}

// Native modal <dialog>: showModal() traps focus and makes the page inert, Escape closes it, and focus returns to the
// button that opened it (same pattern as review/ConfirmDialog).
function ImageLightbox({ open, onClose, url, label }: { open: boolean; onClose: () => void; url: string; label: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      opener.current = document.activeElement;
      d.showModal();
      closeBtn.current?.focus();
    } else if (!open && d.open) {
      d.close();
    }
  }, [open]);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const onDialogClose = () => {
      onClose();
      if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus();
    };
    d.addEventListener("close", onDialogClose);
    return () => d.removeEventListener("close", onDialogClose);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-label={`Original image: ${label}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) e.currentTarget.close();
      }}
      className="m-auto max-h-[95vh] w-[min(64rem,calc(100vw-2rem))] rounded-xl border border-subtle bg-card shadow-card p-0 text-ink shadow-xl backdrop:bg-[#0a3f40]/70"
    >
      <div className="flex items-center justify-between gap-2 border-b border-subtle px-4 py-2">
        <p className="font-bold">Original image · {label}</p>
        <button ref={closeBtn} type="button" onClick={() => ref.current?.close()} className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-line px-3 font-bold">
          <Icon name="cross" size={16} /> Close
        </button>
      </div>
      <div className="flex justify-center bg-page p-2">
        {open && (
          // eslint-disable-next-line @next/next/no-img-element -- authenticated, no-store image via the same-origin proxy
          <img src={url} alt={`Uploaded ${label} as saved for the reviewer`} className="max-h-[80vh] w-auto max-w-full object-contain" />
        )}
      </div>
    </dialog>
  );
}
