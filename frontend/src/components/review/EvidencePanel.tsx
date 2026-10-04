"use client";

// Source evidence for the reviewer, from the existing view-only endpoints: AI-extracted values with their quoted
// evidence (`/ai/reviewed`), checked lab-report values with page regions (`/documents/reviewed`), and voice values
// confirmed by a health worker (`/voice/prefill`). Each item says where it came from and who checked it. None of
// these change urgency: they are evidence beside the rules result, not inputs to it. When a source cannot be
// loaded or was never recorded, the panel says so instead of inventing a citation.
import { useEffect, useState } from "react";

import { EvidenceViewer } from "@/components/EvidenceViewer";
import { Icon } from "@/components/Icon";
import { ProvenanceBadge, type Provenance } from "@/components/review/Badges";
import { Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { Region } from "@/lib/evidence";
import { roleLabel, voiceFieldWords, voiceSourceWords } from "@/lib/review";

type AiValue = { field_id: string; field: string; value: unknown; basis: string; reviewed_by_role?: string; evidence?: { segment_id: string; quote: string }[] };
type AiReviewedResp = { values: AiValue[]; unresolved: { field_id: string; field: string; status: string; priority_review: boolean; state: string }[]; conflicting_readings: string[] };
type DocValue = { field_id: string; name: string; outcome: string; value: Record<string, unknown> | null; source: { type: string; document_id: string; page_index: number; regions: Region[]; resolved_by_role: string | null } };
type DocsResp = { values: DocValue[]; unresolved: { document_id: string; field_id?: string; state: string }[] };
type VoiceSource = { type: string; transcription_id: string; transcript_chars: [number, number]; resolved_by_role: string };
// Shape from backend app/voice/service.py prefill + readback.assemble_prefill: `values` = one agreed value per field;
// `conflicts` = confirmed readings that disagree (no value chosen by the server); `unresolved` = not yet decided.
type VoiceResp = {
  values: Record<string, { values: Record<string, unknown>; sources: VoiceSource[] }>;
  conflicts?: Record<string, { values: Record<string, unknown>; source: VoiceSource }[]>;
  unresolved?: { candidate_id: string; field: string; transcription_id: string; state: string }[];
};

type Slot<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "off"; why: string };

function why(err: unknown, what: string): string {
  if (err instanceof ApiError) {
    if (err.code === "FEATURE_DISABLED" || err.status === 503) return `${what}: this input is turned off on this server.`;
    if (err.code?.startsWith("CONSENT")) return `${what}: consent is not in effect, so it is not shown.`;
    if (err.status === 404) return `${what}: nothing recorded for this case.`;
  }
  return `${what}: could not be loaded. Source unavailable.`;
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "object") return String(v);
  const o = v as Record<string, unknown>;
  if ("sbp" in o && "dbp" in o) return `${o.sbp}/${o.dbp} mmHg`;
  if ("value" in o || "qualitative" in o) return [o.comparator, o.qualitative ?? o.value, o.unit].filter(Boolean).join(" ");
  if ("flag" in o) return String(o.flag).replaceAll("_", " ");
  return Object.entries(o)
    .filter(([, x]) => x !== null && x !== undefined && typeof x !== "object")
    .map(([k, x]) => `${k.replaceAll("_", " ")}: ${x}`)
    .join(", ");
}

function aiKind(basis: string): Provenance {
  return basis === "reviewer_corrected" ? "ai_corrected" : basis === "document_review" ? "ocr_reviewed" : "ai_reviewed";
}

export function EvidencePanel({ caseId, consent, clinical }: { caseId: string; consent: Record<string, string>; clinical: boolean }) {
  const [ai, setAi] = useState<Slot<AiReviewedResp>>({ state: "loading" });
  const [docs, setDocs] = useState<Slot<DocsResp>>({ state: "loading" });
  const [voice, setVoice] = useState<Slot<VoiceResp>>({ state: "loading" });
  const aiAllowed = consent.ai_assist === "granted";

  useEffect(() => {
    if (!clinical) return;
    let live = true;
    setAi({ state: "loading" });
    setDocs({ state: "loading" });
    setVoice({ state: "loading" });
    if (aiAllowed) {
      api.get<AiReviewedResp>(`cases/${caseId}/ai/reviewed`).then((d) => live && setAi({ state: "ok", data: d }), (e) => live && setAi({ state: "off", why: why(e, "AI-extracted values") }));
    } else setAi({ state: "off", why: "AI assistance was not agreed for this case, so no AI-extracted values exist." });
    api.get<DocsResp>(`cases/${caseId}/documents/reviewed`).then((d) => live && setDocs({ state: "ok", data: d }), (e) => live && setDocs({ state: "off", why: why(e, "Lab-report values") }));
    api.get<VoiceResp>(`cases/${caseId}/voice/prefill`).then((d) => live && setVoice({ state: "ok", data: d }), (e) => live && setVoice({ state: "off", why: why(e, "Voice values") }));
    return () => {
      live = false;
    };
  }, [caseId, clinical, aiAllowed]);

  if (!clinical) {
    return <p className="text-sm text-muted">Triage consent is not in effect, so source evidence is not shown. The urgency and RED status stay visible for safety.</p>;
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">
        Evidence sits beside the rules result. None of it changes urgency. Recorded triage input stores a per-field source only for values a reviewer
        corrected, so values below are shown with their own source, not matched to the triage input automatically.
      </p>

      <Group title="AI-extracted from text" icon="sparkle" slot={ai}>
        {(d) => (
          <>
            {d.unresolved.length > 0 && (
              <ul className="space-y-1">
                {d.unresolved.map((u) => (
                  <li key={u.field_id} className="rounded border border-dashed border-ai bg-ai-bg px-2 py-1.5 text-sm">
                    <div className="flex flex-wrap items-center gap-1">
                      <strong>{u.field.replaceAll("_", " ")}</strong>
                      <ProvenanceBadge kind="ai_suggested" detail={u.status === "disputed" || u.status === "disputed_raise" ? "passes disagreed" : u.state} />
                    </div>
                    <p className="text-xs text-ink">Not reviewed by a person; not used anywhere until a health worker or doctor decides on it.</p>
                  </li>
                ))}
              </ul>
            )}
            {d.values.length === 0 && d.unresolved.length === 0 && <Empty>No AI-extracted values for this case.</Empty>}
            <ul className="space-y-1.5">
              {d.values.map((v) => (
                <li key={v.field_id} className="rounded border border-subtle px-2 py-1.5 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-1">
                    <strong>{v.field.split("#")[0].replaceAll("_", " ")}</strong>
                    <span className="font-mono">{show(v.value)}</span>
                  </div>
                  <ProvenanceBadge kind={aiKind(v.basis)} detail={v.reviewed_by_role ? roleLabel(v.reviewed_by_role) : undefined} />
                  {v.evidence && v.evidence.length > 0 ? (
                    <details className="mt-1 text-xs">
                      <summary className="cursor-pointer py-0.5 font-bold text-primary">Source quote</summary>
                      {v.evidence.map((e, i) => (
                        <blockquote key={i} className="mt-1 border-l-4 border-ai pl-2 italic">
                          “{e.quote}” <span className="not-italic text-muted">({e.segment_id})</span>
                        </blockquote>
                      ))}
                    </details>
                  ) : (
                    <p className="mt-1 text-xs text-muted">Source unavailable for this value.</p>
                  )}
                </li>
              ))}
            </ul>
            {d.conflicting_readings.length > 0 && <p className="text-xs text-warning">These readings disagree — none was picked automatically: {d.conflicting_readings.join(", ")}.</p>}
          </>
        )}
      </Group>

      <Group title="Lab report (OCR)" icon="document" slot={docs}>
        {(d) => (
          <>
            {d.values.length === 0 && <Empty>No checked lab-report values.</Empty>}
            <ul className="space-y-1.5">
              {d.values.map((v) => (
                <DocItem key={v.field_id} caseId={caseId} v={v} />
              ))}
            </ul>
            {d.unresolved.length > 0 && <p className="text-xs text-warning">{d.unresolved.length} document item(s) not yet checked by a person — not shown as evidence.</p>}
          </>
        )}
      </Group>

      <Group title="Voice (read back and confirmed)" icon="mic" slot={voice}>
        {(d) => {
          const entries = Object.entries(d.values ?? {});
          const conflicts = Object.entries(d.conflicts ?? {});
          const unresolved = d.unresolved ?? [];
          if (entries.length === 0 && conflicts.length === 0 && unresolved.length === 0) return <Empty>No confirmed voice values.</Empty>;
          return (
            <>
              {conflicts.map(([field, readings]) => (
                <section key={`conflict-${field}`} aria-label={`Disagreeing voice readings for ${voiceFieldWords(field)}`} className="rounded border-2 border-warning bg-warning-bg px-2 py-1.5 text-sm">
                  <p className="flex items-center gap-1 font-bold text-warning">
                    <Icon name="alert" size={14} /> {voiceFieldWords(field)}: {readings.length} readings disagree
                  </p>
                  <ul className="mt-1 space-y-1">
                    {readings.map((r, i) => (
                      <li key={`${r.source.transcription_id}-${i}`} className="rounded border border-warning/40 bg-card px-2 py-1">
                        <span className="font-mono font-bold">{show(r.values)}</span>
                        <VoiceSourceLine s={r.source} />
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-xs text-ink">These readings disagree and none was chosen; recheck with the patient. Not used anywhere until a person enters one value.</p>
                </section>
              ))}
              {entries.length > 0 && (
                <ul className="space-y-1.5">
                  {entries.map(([field, x]) => (
                    <li key={field} className="rounded border border-subtle px-2 py-1.5 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-1">
                        <strong>{voiceFieldWords(field)}</strong>
                        <span className="font-mono">{show(x.values)}</span>
                      </div>
                      {x.sources.map((src, i) => (
                        <VoiceSourceLine key={i} s={src} />
                      ))}
                    </li>
                  ))}
                </ul>
              )}
              {unresolved.length > 0 && (
                <ul className="space-y-1">
                  {unresolved.map((u) => (
                    <li key={u.candidate_id} className="rounded border border-dashed border-subtle px-2 py-1.5 text-sm">
                      <strong>{voiceFieldWords(u.field)}</strong> — Not yet confirmed by a person{u.state === "unsure" ? " (marked unsure at read-back)" : ""}. No value is shown or used.
                    </li>
                  ))}
                </ul>
              )}
            </>
          );
        }}
      </Group>
    </div>
  );
}

function VoiceSourceLine({ s }: { s: VoiceSource }) {
  return (
    <p className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-muted">
      <span className="inline-flex items-center gap-1 rounded-full border border-secondary px-2 py-0.5 font-bold text-secondary">
        <Icon name={s.type === "voice_manual_correction" ? "pencil" : "mic"} size={14} />
        {voiceSourceWords(s.type)}
      </span>
      {roleLabel(s.resolved_by_role)} · transcript characters {s.transcript_chars?.[0] ?? "?"}–{s.transcript_chars?.[1] ?? "?"}
    </p>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

function Group<T>({ title, icon, slot, children }: { title: string; icon: "sparkle" | "document" | "mic"; slot: Slot<T>; children: (d: T) => React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h4 className="flex items-center gap-1.5 text-sm font-bold uppercase tracking-wide text-muted">
        <Icon name={icon} size={16} />
        {title}
      </h4>
      {slot.state === "loading" && <Spinner label="Loading…" />}
      {slot.state === "off" && (
        <p className="flex items-center gap-1 text-sm text-muted">
          <Icon name="info" size={14} />
          {slot.why}
        </p>
      )}
      {slot.state === "ok" && children(slot.data)}
    </section>
  );
}

type DocDetail = { document_id: string; pages: { page_index: number; width: number; height: number }[] };

function DocItem({ caseId, v }: { caseId: string; v: DocValue }) {
  const [open, setOpen] = useState(false);
  const [doc, setDoc] = useState<DocDetail | "error" | null>(null);
  useEffect(() => {
    if (!open || doc) return;
    api.get<DocDetail>(`cases/${caseId}/documents/${v.source.document_id}`).then(setDoc, () => setDoc("error"));
  }, [open, doc, caseId, v.source.document_id]);
  const page = doc && doc !== "error" ? (doc.pages.find((p) => p.page_index === v.source.page_index) ?? null) : null;
  return (
    <li className="rounded border border-subtle px-2 py-1.5 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <strong>{v.name}</strong>
        <span className="font-mono">{show(v.value)}</span>
      </div>
      <ProvenanceBadge kind="ocr_reviewed" detail={v.outcome === "corrected" ? `entered from paper by ${roleLabel(v.source.resolved_by_role)}` : roleLabel(v.source.resolved_by_role)} />
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="mt-1 block min-h-8 text-xs font-bold text-primary underline underline-offset-4">
        {open ? "Hide" : "Show"} where it was read (page {v.source.page_index + 1})
      </button>
      {open && (
        <div className="mt-1">
          {doc === null && <Spinner label="Loading page…" />}
          {doc === "error" && <p className="text-xs text-muted">Source unavailable: the page could not be loaded (it may have been deleted under the retention policy).</p>}
          {page && <EvidenceViewer imageUrl={`/api/backend/cases/${caseId}/documents/${v.source.document_id}/pages/${page.page_index}/image`} page={page} regions={v.source.regions ?? []} label={v.name} />}
        </div>
      )}
    </li>
  );
}
