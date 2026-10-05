// Medical-image visual findings (MedGemma pipeline, docs/18). Pure helpers and types shared by the intake upload
// screen, the reviewer evidence panel and the sign-off gate. Nothing here computes or changes urgency: image urgency
// signals are keyword hits shown to a reviewer (raise-only flags), and the model's confidence is a label only — it
// never hides a field, the description or a signal. The product is non-diagnostic; these are visual descriptions.

export type ImageType = "chest_xray" | "ecg_strip" | "ct_report_image" | "wound_photo" | "skin_lesion";
export type ImageStatus = "described" | "not_available" | "unsupported_type" | "failed";
export type NotAvailableReason = "disabled" | "consent_ai_assist_missing" | "synthetic_attestation_missing";
export type ConfidenceBand = "high" | "moderate" | "low";
export type SignalAction = "RED_FLAG" | "YELLOW_FLAG" | "REVIEW_NOTE";
/** Icon keys in components/Icon.tsx (kept as strings so this file stays free of React imports). */
export type ImageIcon = "xray" | "ecg" | "scan" | "bandage" | "skin";

export type UrgencySignal = { signal: string; action: SignalAction; note: string; source: string; rule_set: string; negated: boolean };

export type MedicalImageItem = {
  document_id: string;
  case_id: string;
  pipeline: "medgemma";
  image_class: string;
  declared_type: string;
  classifier_hint: string | null;
  classifier_mismatch: boolean;
  status: ImageStatus;
  note: string | null;
  not_available_reason: NotAvailableReason | null;
  raw_description: string | null;
  structured_fields: Record<string, string>;
  withheld: { fields: string[]; description: boolean; reasons: string[] };
  urgency_signals: UrgencySignal[];
  confidence: number | null;
  confidence_band: ConfidenceBand | null;
  disclaimer: string;
  keyword_rules_validated: boolean;
  source_image_url: string;
  backend: string;
  model: string;
  prompt_version: string;
  guard_version: string;
  rule_sets_run: string[];
  requires_acknowledgement: boolean;
  acknowledged: boolean;
  created_at: string;
  completed_at: string | null;
};

export type MedicalImagesResp = { case_id: string; medical_images: MedicalImageItem[] };

export const IMAGE_TYPES: { key: ImageType; label: string; icon: ImageIcon }[] = [
  { key: "chest_xray", label: "Chest X-ray", icon: "xray" },
  { key: "ecg_strip", label: "ECG strip", icon: "ecg" },
  { key: "ct_report_image", label: "CT scan image", icon: "scan" },
  { key: "wound_photo", label: "Wound photo", icon: "bandage" },
  { key: "skin_lesion", label: "Skin lesion", icon: "skin" },
];

export const IMAGE_MIME_TYPES = ["image/png", "image/jpeg"];

/** Shown when the server sent no disclaimer (older backend); the disclaimer box is never left empty. */
export const FALLBACK_DISCLAIMER =
  "AI-described visual findings — not a diagnosis. A qualified clinician must review the original image before any decision.";

export function imageTypeLabel(key: string | null | undefined): string {
  if (!key) return "Unknown type";
  return IMAGE_TYPES.find((t) => t.key === key)?.label ?? key.replaceAll("_", " ");
}

export function imageTypeIcon(key: string | null | undefined): ImageIcon {
  return IMAGE_TYPES.find((t) => t.key === key)?.icon ?? "scan";
}

/** The server's band when it sent one; otherwise high > 0.8, moderate 0.5–0.8, low < 0.5. Null when unknown. */
export function confidenceBand(confidence: number | null | undefined, serverBand?: ConfidenceBand | null): ConfidenceBand | null {
  if (serverBand === "high" || serverBand === "moderate" || serverBand === "low") return serverBand;
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) return null;
  if (confidence > 0.8) return "high";
  if (confidence >= 0.5) return "moderate";
  return "low";
}

/** A signal raises attention only when it is a RED/YELLOW flag that was not negated in the text. */
function raises(s: UrgencySignal, action: SignalAction): boolean {
  return s.action === action && !s.negated;
}

export function hasRedFlag(signals: UrgencySignal[] | null | undefined): boolean {
  return (signals ?? []).some((s) => raises(s, "RED_FLAG"));
}

export function hasYellowFlag(signals: UrgencySignal[] | null | undefined): boolean {
  return (signals ?? []).some((s) => raises(s, "YELLOW_FLAG"));
}

/** REVIEW_NOTE and negated hits: listed quietly for the reviewer, never escalated. */
export function notedSignals(signals: UrgencySignal[] | null | undefined): UrgencySignal[] {
  return (signals ?? []).filter((s) => s.action === "REVIEW_NOTE" || s.negated);
}

export function raisingSignals(signals: UrgencySignal[] | null | undefined): UrgencySignal[] {
  return (signals ?? []).filter((s) => (s.action === "RED_FLAG" || s.action === "YELLOW_FLAG") && !s.negated);
}

const NOT_AVAILABLE_TEXT: Record<NotAvailableReason, string> = {
  disabled: "Medical image AI is not enabled. Your image has been saved for the doctor to review directly.",
  consent_ai_assist_missing:
    "AI assistance was not agreed for this case, so the image was not sent for an AI description. Your image has been saved for the doctor to review directly.",
  synthetic_attestation_missing:
    "In this prototype, cloud AI only describes images marked as synthetic / test images. Your image has been saved for the doctor to review directly.",
};

/** Plain-language status line; null when the image was described (the findings speak for themselves). */
export function statusCopy(item: Pick<MedicalImageItem, "status" | "not_available_reason" | "note">): string | null {
  switch (item.status) {
    case "described":
      return null;
    case "not_available":
      return (item.not_available_reason && NOT_AVAILABLE_TEXT[item.not_available_reason]) || item.note || "An AI description is not available. Your image has been saved for the doctor to review directly.";
    case "failed":
      return "The image could not be described; it has been saved for the reviewer.";
    case "unsupported_type":
      return "This image type cannot be described by the AI; it has been saved for the reviewer.";
    default:
      return item.note || "An AI description is not available; the image has been saved for the reviewer.";
  }
}

export function awaitingAck(item: Pick<MedicalImageItem, "requires_acknowledgement" | "acknowledged">): boolean {
  return !!item.requires_acknowledgement && !item.acknowledged;
}

/** Images whose findings a reviewer still has to mark as reviewed before sign-off (the server enforces this too). */
export function pendingAckCount(items: Pick<MedicalImageItem, "requires_acknowledgement" | "acknowledged">[] | null | undefined): number {
  return (items ?? []).filter(awaitingAck).length;
}

export function needsAck(items: Pick<MedicalImageItem, "requires_acknowledgement" | "acknowledged">[] | null | undefined): boolean {
  return pendingAckCount(items) > 0;
}

const ACRONYMS = new Set(["st", "qrs", "pr", "qt", "qtc", "ecg", "ct", "av", "rv", "lv", "ra", "la", "hr", "ap", "pa", "cp"]);
const UNITS: Record<string, string> = { bpm: "bpm", ms: "ms", mm: "mm", cm: "cm", mmhg: "mmHg", pct: "%", percent: "%", deg: "°" };

/** rate_bpm → "Rate (bpm)", st_segment → "ST segment", qtc_ms → "QTc (ms)". */
export function humanizeField(key: string): string {
  const parts = key.split(/[_\s]+/).filter(Boolean);
  if (parts.length === 0) return key;
  let unit: string | null = null;
  if (parts.length > 1 && UNITS[parts[parts.length - 1].toLowerCase()]) unit = UNITS[parts.pop()!.toLowerCase()];
  const words = parts.map((p, i) => {
    const lower = p.toLowerCase();
    if (lower === "qtc") return "QTc";
    if (ACRONYMS.has(lower)) return lower.toUpperCase();
    return i === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
  });
  return `${words.join(" ")}${unit ? ` (${unit})` : ""}`;
}

/** Fields safe to show: never a field the non-diagnostic filter withheld, even if a value slipped through. */
export function visibleFields(item: Pick<MedicalImageItem, "structured_fields" | "withheld">): [string, string][] {
  const withheld = new Set(item.withheld?.fields ?? []);
  return Object.entries(item.structured_fields ?? {}).filter(([k, v]) => !withheld.has(k) && v !== null && v !== undefined && String(v).trim() !== "");
}

export function withheldSummary(withheld: MedicalImageItem["withheld"] | null | undefined): string | null {
  if (!withheld) return null;
  const n = withheld.fields?.length ?? 0;
  if (n === 0 && !withheld.description) return null;
  const reasons = (withheld.reasons ?? []).map((r) => r.replaceAll("_", " ")).join(", ");
  const why = reasons ? ` (${reasons})` : "";
  const subject = n > 0 ? `${n} field${n === 1 ? "" : "s"}${withheld.description ? " and the description" : ""}` : "The description";
  return `${subject} withheld by the non-diagnostic filter${why}. Withheld text is never shown.`;
}

/** Only same-origin proxy URLs are rendered as an image source; anything else is treated as unavailable. */
export function safeImageUrl(url: string | null | undefined): string | null {
  if (typeof url !== "string" || !url.startsWith("/api/backend/") || url.includes("..") || url.includes("//", 1)) return null;
  return url;
}

/** The capability keys this screen reads; every MedGemma key is optional because an older backend omits them. */
export type ImageCaps = {
  ocr_enabled: boolean;
  document_types: Record<string, boolean>;
  medgemma_enabled?: boolean;
  medgemma_backend?: string;
  medgemma_model?: string;
  medgemma_ready?: boolean;
  medgemma_cloud?: boolean;
  supported_image_types?: string[];
};

/**
 * Whether an image type can be uploaded, with the reason when not. A type the server supports stays selectable when
 * the AI is off: the image is still saved for the doctor (status not_available), so the reviewer can see it.
 */
export function imageTypeAvailability(caps: ImageCaps | null | undefined, key: ImageType): { ok: boolean; reason: string | null } {
  if (!caps) return { ok: false, reason: "loading server settings" };
  if (!caps.ocr_enabled) return { ok: false, reason: "document upload is turned off on this server" };
  if (Array.isArray(caps.supported_image_types)) {
    return caps.supported_image_types.includes(key) ? { ok: true, reason: null } : { ok: false, reason: "not supported by this server" };
  }
  if (caps.document_types?.[key] === true) return { ok: true, reason: null };
  return { ok: false, reason: "this server does not accept medical images yet" };
}

/** One line about what happens to an uploaded image, from the capabilities (never claims more than they say). */
export function aiAvailabilityNote(caps: ImageCaps | null | undefined): string | null {
  if (!caps || !caps.ocr_enabled) return null;
  if (!caps.medgemma_enabled) return "Medical image AI is not enabled on this server. Images are saved for the doctor to review directly, without an AI description.";
  if (caps.medgemma_ready === false) return "Medical image AI is enabled but not ready. Images are saved; an AI description may not be produced.";
  if (caps.medgemma_cloud) {
    return `Images marked as synthetic / test images are sent to a cloud AI service (${caps.medgemma_backend ?? "configured backend"}${caps.medgemma_model ? `, ${caps.medgemma_model}` : ""}) for a visual description. Other images are saved without one.`;
  }
  return `Images are described by ${caps.medgemma_backend === "fake" ? "a canned offline demo backend (not a real model)" : (caps.medgemma_backend ?? "the configured backend")}.`;
}
