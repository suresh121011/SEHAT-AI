"use client";

// Medical image upload (MedGemma visual findings, docs/18): image type, drag-and-drop or file picker, preview, and the
// per-upload synthetic-image attestation that cloud AI needs in this prototype. PNG/JPEG only. The type the health
// worker picks is authoritative; the server only cross-checks it against the filename and flags a mismatch.
import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { IMAGE_MIME_TYPES, IMAGE_TYPES, type ImageType } from "@/lib/medicalImages";

type Props = {
  maxBytes: number;
  availability: Record<ImageType, { ok: boolean; reason: string | null }>;
  /** Cloud AI is configured: show the synthetic / test image checkbox. */
  cloud: boolean;
  /** Resolves true when the server accepted the image (errors are shown by the page). */
  onUpload: (file: File, type: ImageType, synthetic: boolean | null, signal: AbortSignal) => Promise<boolean>;
};

export function MedicalImageUpload({ maxBytes, availability, cloud, onUpload }: Props) {
  const firstOk = IMAGE_TYPES.find((t) => availability[t.key]?.ok)?.key ?? "";
  const [type, setType] = useState<ImageType | "">(firstOk);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [synthetic, setSynthetic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [cameraOk, setCameraOk] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => setCameraOk(window.isSecureContext), []);
  // Keep the selection valid when the server settings arrive or change.
  useEffect(() => {
    if (!type || !availability[type]?.ok) setType(firstOk);
  }, [availability, firstOk, type]);
  // Object URL for the preview; revoked when the file changes or the component unmounts.
  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const meta = IMAGE_TYPES.find((t) => t.key === type);

  function choose(f: File | undefined | null) {
    setError(null);
    if (!f) return;
    if (!IMAGE_MIME_TYPES.includes(f.type)) {
      setFile(null);
      setError("Please choose a PNG or JPEG image.");
      return;
    }
    if (f.size > maxBytes) {
      setFile(null);
      setError(`The image is too large (limit ${Math.round(maxBytes / 1024 / 1024)} MB).`);
      return;
    }
    setFile(f);
  }

  async function submit() {
    if (!file || !type || busy) return;
    abort.current = new AbortController();
    setBusy(true);
    setError(null);
    try {
      if (await onUpload(file, type, cloud ? synthetic : null, abort.current.signal)) {
        setFile(null);
        setSynthetic(false);
        if (inputRef.current) inputRef.current.value = "";
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded border border-warning/60 p-4">
      <div className="space-y-1">
        <label htmlFor="image-type" className="block font-bold">
          What kind of image is it?
        </label>
        <select
          id="image-type"
          value={type}
          disabled={busy}
          onChange={(e) => setType(e.target.value as ImageType)}
          className="block min-h-11 w-full max-w-md rounded border-2 border-line bg-card px-3 py-2 text-base"
        >
          {!firstOk && <option value="">No image type is available</option>}
          {IMAGE_TYPES.map((t) => {
            const a = availability[t.key];
            return (
              <option key={t.key} value={t.key} disabled={!a?.ok}>
                {t.label}
                {a?.ok ? "" : ` — not available (${a?.reason ?? "unknown"})`}
              </option>
            );
          })}
        </select>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy && type) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!busy && type) choose(e.dataTransfer.files?.[0]);
        }}
        className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 text-center ${dragging ? "border-warning bg-warning-bg" : "border-line bg-page"} ${!type ? "opacity-55" : ""}`}
      >
        <Icon name={meta?.icon ?? "image"} size={40} className="text-warning" />
        <p className="font-bold">{meta ? `Drop the ${meta.label.toLowerCase()} here` : "Drop the image here"}</p>
        <p className="text-sm text-muted">or</p>
        <div className="flex flex-wrap justify-center gap-3">
          {cameraOk && (
            <label className={`inline-flex min-h-11 cursor-pointer items-center rounded bg-primary px-4 py-2 font-bold text-white ${busy || !type ? "pointer-events-none opacity-55" : ""}`}>
              Take photo
              <input type="file" accept="image/png,image/jpeg" capture="environment" className="sr-only" disabled={busy || !type} onChange={(e) => choose(e.target.files?.[0])} />
            </label>
          )}
          <label className={`inline-flex min-h-11 cursor-pointer items-center rounded border-2 border-primary bg-card px-4 py-2 font-bold text-primary ${busy || !type ? "pointer-events-none opacity-55" : ""}`}>
            Choose image
            <input ref={inputRef} type="file" accept="image/png,image/jpeg" className="sr-only" disabled={busy || !type} onChange={(e) => choose(e.target.files?.[0])} />
          </label>
        </div>
        <p className="text-xs text-muted">PNG or JPEG, up to {Math.round(maxBytes / 1024 / 1024)} MB.</p>
      </div>

      {file && preview && (
        <div className="flex flex-wrap items-start gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element -- local object URL preview of the chosen file */}
          <img src={preview} alt={`Preview of the chosen ${meta?.label.toLowerCase() ?? "image"}`} className="h-28 w-28 rounded border border-subtle object-cover" />
          <p className="text-sm">
            <span className="font-bold">{busy ? "Uploading:" : "Chosen:"}</span> <span className="break-all">{file.name}</span>
            <span className="block text-muted">
              {file.type === "image/png" ? "PNG" : "JPEG"} · {(file.size / 1024 / 1024).toFixed(1)} MB
            </span>
          </p>
        </div>
      )}

      {cloud && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={synthetic} disabled={busy} onChange={(e) => setSynthetic(e.target.checked)} className="mt-1 size-5 accent-primary" />
          <span>
            This is a synthetic / test image (required for cloud AI in this prototype)
            <span className="block text-xs text-muted">Leave unticked for a real patient image: it is saved for the doctor and not sent to the cloud AI.</span>
          </span>
        </label>
      )}

      <button
        type="button"
        onClick={submit}
        disabled={!file || !type || busy}
        className="inline-flex min-h-11 items-center gap-2 rounded bg-primary px-5 py-2 font-bold text-white disabled:cursor-not-allowed disabled:opacity-55"
      >
        <Icon name="upload" size={16} /> {busy ? "Uploading…" : "Upload image"}
      </button>
      {busy && (
        <p aria-live="polite" className="text-sm">
          Saving the image and asking for a visual description… this can take up to half a minute.
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
