import { Icon, type IconName } from "@/components/Icon";

// Where a value came from, in plain words. Role codes are mapped to people, never shown raw; nothing says
// "patient confirmed" because the backend records who decided (the account role), not who spoke.
export type SourceKind = "voice" | "voice_corrected" | "ai_pending" | "ai_reviewed" | "ai_corrected" | "document" | "document_corrected" | "typed" | "local_note";

const SOURCE: Record<SourceKind, { label: string; icon: IconName; cls: string }> = {
  voice: { label: "From voice, checked by health worker", icon: "mic", cls: "border-secondary text-secondary" },
  voice_corrected: { label: "Voice value corrected by health worker", icon: "pencil", cls: "border-secondary text-secondary" },
  ai_pending: { label: "AI-extracted, needs your decision", icon: "sparkle", cls: "border-ai text-ai border-dashed" },
  ai_reviewed: { label: "AI-extracted, accepted by health worker", icon: "sparkle", cls: "border-ai text-ai border-dashed" },
  ai_corrected: { label: "AI-extracted, corrected by health worker", icon: "sparkle", cls: "border-ai text-ai border-dashed" },
  document: { label: "From lab report, checked by health worker", icon: "document", cls: "border-info text-info" },
  document_corrected: { label: "Lab report value entered by health worker", icon: "document", cls: "border-info text-info" },
  typed: { label: "Typed on this form", icon: "pencil", cls: "border-line text-muted" },
  local_note: { label: "Note on this device only, not saved", icon: "lock", cls: "border-line text-muted" },
};

export function SourceTag({ kind, detail }: { kind: SourceKind; detail?: string }) {
  const s = SOURCE[kind];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5 text-sm ${s.cls}`} title={detail}>
      <Icon name={s.icon} size={14} />
      {s.label}
    </span>
  );
}

const ROLE_WORDS: Record<string, string> = { anm: "health worker", medical_officer: "doctor", patient: "patient account", supervisor: "supervisor" };
export function roleWords(role: string | null | undefined): string {
  return role ? (ROLE_WORDS[role] ?? role) : "unknown";
}
