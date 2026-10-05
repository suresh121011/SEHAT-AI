// Urgency and provenance badges for the reviewer dashboard. Never colour alone: every badge has an icon shape and
// words. GREEN says "routine", never "safe". Provenance kinds are only those the backend actually records.
import { Icon, type IconName } from "@/components/Icon";
import type { Urgency } from "@/lib/review";

const URGENCY: Record<Urgency, { word: string; icon: IconName; cls: string }> = {
  RED: { word: "RED", icon: "octagon", cls: "bg-urg-red text-urg-red-ink border-urg-red" },
  YELLOW: { word: "YELLOW", icon: "alert", cls: "bg-urg-yellow text-urg-yellow-ink border-urg-yellow-ink/40" },
  GREEN: { word: "GREEN", icon: "circle", cls: "bg-urg-green text-urg-green-ink border-urg-green-ink/40" },
};

export const URGENCY_MEANING: Record<Urgency, string> = {
  RED: "immediate attention",
  YELLOW: "urgent review",
  GREEN: "routine queue — not a statement that the patient is well",
};

export function UrgencyBadge({ urgency, size = "md", label }: { urgency: Urgency; size?: "sm" | "md" | "lg"; label?: string }) {
  const u = URGENCY[urgency];
  const pad = size === "lg" ? "px-3 py-1.5 text-lg gap-2" : size === "sm" ? "px-1.5 py-0.5 text-xs gap-1" : "px-2 py-1 text-sm gap-1.5";
  return (
    <span className={`inline-flex items-center rounded border font-bold tracking-wide ${pad} ${u.cls}`}>
      <Icon name={u.icon} size={size === "lg" ? 20 : size === "sm" ? 14 : 16} />
      {u.word}
      {label && <span className="font-normal">{label}</span>}
    </span>
  );
}

/** Outlined urgency word for hypothetical results: never the real urgency fill, so it cannot be mistaken for one. */
export function HypotheticalUrgency({ urgency }: { urgency: Urgency | string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border-2 border-dashed border-hypo-line bg-card px-2 py-0.5 text-sm font-bold text-ink">
      <Icon name="question" size={14} />
      {urgency} <span className="font-normal">(hypothetical)</span>
    </span>
  );
}

export type Provenance =
  | "rules_engine" | "recorded_input" | "ai_suggested" | "ai_reviewed" | "ai_corrected" | "keyword_suggested" | "keyword_reviewed" | "ocr_reviewed" | "reviewer_action"
  | "unavailable";

const PROV: Record<Provenance, { label: string; icon: IconName; cls: string }> = {
  rules_engine: { label: "Rules engine", icon: "scale", cls: "border-primary text-primary" },
  recorded_input: { label: "Recorded triage input", icon: "clipboard", cls: "border-line text-ink" },
  ai_suggested: { label: "AI-suggested, not reviewed", icon: "sparkle", cls: "border-ai text-ai border-dashed bg-ai-bg" },
  ai_reviewed: { label: "AI-extracted, accepted by a person", icon: "sparkle", cls: "border-ai text-ai border-dashed" },
  ai_corrected: { label: "AI-extracted, corrected by a person", icon: "pencil", cls: "border-ai text-ai border-dashed" },
  // Deterministic red-flag phrase match (docs/16 §2c): never labelled as AI.
  keyword_suggested: { label: "Keyword rule — not AI model output, not reviewed", icon: "info", cls: "border-warning text-ink border-dashed" },
  keyword_reviewed: { label: "Keyword rule — not AI model output, accepted by a person", icon: "info", cls: "border-warning text-ink" },
  ocr_reviewed: { label: "Lab report (OCR), checked by a person", icon: "document", cls: "border-info text-info" },
  reviewer_action: { label: "Human reviewer", icon: "people", cls: "border-human text-human bg-human-bg" },
  unavailable: { label: "Source unavailable", icon: "info", cls: "border-line text-muted border-dotted" },
};

export function ProvenanceBadge({ kind, detail }: { kind: Provenance; detail?: string }) {
  const p = PROV[kind];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5 text-xs font-bold ${p.cls}`}>
      <Icon name={p.icon} size={14} />
      {p.label}
      {detail && <span className="font-normal">· {detail}</span>}
    </span>
  );
}

/** One status-pill component for every reviewer screen (GOV.UK / NHS "tag" guidance: adjectives, not verbs; never
 * interactive; the same status keeps the same tone everywhere; colour never alone — always an icon and words).
 * Tones: `neutral` default state (de-emphasised) · `attention` needs information (amber) · `done` a recorded human
 * action (teal, never green) · `danger` RED-related only (red is reserved for urgency and danger). Pills stay on one
 * line when they fit; a long label wraps rather than being clipped, so no text is ever hidden. */
export type PillTone = "neutral" | "attention" | "done" | "danger";

const PILL: Record<PillTone, string> = {
  neutral: "border-subtle text-muted bg-card",
  attention: "border-warning/50 text-warning bg-warning-bg",
  done: "border-human text-human bg-human-bg",
  danger: "border-error/50 text-error bg-error-bg",
};

export function StatusPill({ tone, icon, children }: { tone: PillTone; icon: IconName; children: React.ReactNode }) {
  return (
    <span className={`inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-bold leading-5 ${PILL[tone]}`}>
      <Icon name={icon} size={14} />
      <span className="min-w-0">{children}</span>
    </span>
  );
}
