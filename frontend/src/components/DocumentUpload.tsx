"use client";

import { useEffect, useRef, useState } from "react";

// docs/09 §5.1 document upload: type selector, camera and file picker, progress, explicit errors.
// The file goes only to this SEHAT server (local OCR); nothing is sent to an outside company.

export type DocType = "lab_report" | "prescription" | "discharge_summary";
export const DOC_TYPE_LABEL: Record<DocType, string> = {
  lab_report: "Lab report (printed)",
  prescription: "Prescription (handwritten)",
  discharge_summary: "Discharge summary",
};

type Props = {
  maxBytes: number;
  available: Record<string, boolean>;
  disabled?: boolean;
  onUpload: (file: File, type: DocType, signal: AbortSignal) => Promise<void>;
};

export function DocumentUpload({ maxBytes, available, disabled, onUpload }: Props) {
  const [type, setType] = useState<DocType>("lab_report");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  // Phone cameras need a secure (HTTPS) page; on plain-HTTP dev servers only the file picker is offered.
  const [cameraOk, setCameraOk] = useState(false);
  useEffect(() => setCameraOk(window.isSecureContext), []);

  async function pick(file: File | undefined) {
    setError(null);
    if (!file) return;
    if (!["image/png", "image/jpeg", "application/pdf"].includes(file.type)) {
      setError("Please choose a PNG or JPEG photo, or a PDF.");
      return;
    }
    if (file.size > maxBytes) {
      setError(`The file is too large (limit ${Math.round(maxBytes / 1024 / 1024)} MB).`);
      return;
    }
    abort.current = new AbortController();
    setBusy(true);
    try {
      await onUpload(file, type, abort.current.signal);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded border border-subtle p-4">
      <fieldset className="space-y-2">
        <legend className="font-bold">What kind of document is it?</legend>
        <div className="flex flex-wrap gap-2">
          {(["lab_report", "prescription", "discharge_summary"] as const).map((t) => {
            const ok = available[t] ?? false;
            return (
              <button
                key={t}
                type="button"
                aria-pressed={type === t}
                disabled={!ok || disabled || busy}
                onClick={() => ok && setType(t)}
                className={`rounded border px-3 py-1.5 text-sm ${type === t ? "border-primary bg-primary text-white" : "border-line "} disabled:opacity-50`}
              >
                {DOC_TYPE_LABEL[t]}
                {!ok && <span className="ml-1 text-xs">(not available)</span>}
              </button>
            );
          })}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-3">
        {cameraOk && (
          <label className={`cursor-pointer rounded bg-primary px-4 py-2 font-medium text-white ${busy || disabled ? "pointer-events-none text-muted" : ""}`}>
            Take photo
            <input type="file" accept="image/*" capture="environment" className="sr-only" disabled={busy || disabled} onChange={(e) => pick(e.target.files?.[0])} />
          </label>
        )}
        <label className={`cursor-pointer rounded border border-line px-4 py-2 ${busy || disabled ? "pointer-events-none text-muted" : ""}`}>
          Choose file
          <input type="file" accept="image/png,image/jpeg,application/pdf" className="sr-only" disabled={busy || disabled} onChange={(e) => pick(e.target.files?.[0])} />
        </label>
      </div>
      <p className="text-xs text-muted">PNG, JPEG or PDF, up to 5 pages. Read on this server only. Keep the page flat, well lit and in focus.</p>
      {busy && (
        <p aria-live="polite" className="text-sm">
          Reading the document… handwriting can take up to a minute on this computer.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
    </div>
  );
}
