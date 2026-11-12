"use client";

// Clinical encounter summary (backend app/ai/encounter_summary.py, docs/19). One case-level, source-linked view of
// everything recorded: needs-attention first, the rules engine's urgency kept apart from the drafted text, then each
// section as rows with a trust level and their sources. Nothing here computes urgency or picks between conflicting
// values; accepting the summary changes nothing in triage. Not a diagnosis.
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { ProvenanceBadge, StatusPill, UrgencyBadge } from "@/components/review/Badges";
import { Eyebrow, SectionCard } from "@/components/review/Panel";
import { Button, Notice, Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  AGREEMENT_WORDS, READINESS_WORDS, TRUST_WORDS, acceptState, groupWithheld, verificationBase, attentionTone, documentLink, factsFor, flagWords, machineAgreed, ocrAgreement,
  readinessReasonWords, reviewWords, sortedAttention, sourceName, sourceWords, verifyLink, withheldReasonWords,
  imageDescribedBy, imageFieldRows, imageNeedsReview, imageValue, noteSections, shouldAutoBuild, type NoteLine,
  type EncounterSummary, type EvidenceStatus, type Fact, type Row, type SummaryView, type Trust, type UnmatchedDocument, type VerifyPage, type Withheld,
} from "@/lib/encounterSummary";

function explain(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "SUMMARY_STALE") return "Information changed after this summary was made. Refresh it, then review the new one.";
    if (err.code === "SUMMARY_SUPERSEDED") return "A newer summary exists. Reload to review it.";
    if (err.code === "REVIEW_CONFLICT") return "Someone else reviewed this summary in the meantime. Reload.";
    if (err.code === "CONFLICTS_NOT_ACKNOWLEDGED") return "Tick the box to confirm you have seen the conflicting values.";
    if (err.code === "SUMMARY_PARTIAL") return "A source could not be read, so this summary cannot be accepted.";
    if (err.status === 403) return "Consent for triage is not in effect, or you cannot review this case.";
    return err.message;
  }
  return "The summary could not be loaded. Check the connection and try again.";
}

function TrustPill({ trust }: { trust: Trust }) {
  const t = TRUST_WORDS[trust];
  return <StatusPill tone={t.tone} icon={t.icon}>{t.label}</StatusPill>;
}

function Sources({ facts }: { facts: Fact[] }) {
  const all = facts.flatMap((f) => f.sources.map((s) => ({ s, f })));
  if (!all.length) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-primary">Sources ({all.length})</summary>
      <ul className="mt-1 space-y-0.5 pl-3">
        {all.map(({ s, f }, i) => (
          <li key={i}>
            {sourceWords(s)} <span className="text-muted">· {TRUST_WORDS[f.trust].label.toLowerCase()}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

// The verification page the viewer's role may open (ANM: documents step; medical officer: /dashboard/verify), or null.
const VerifyAccess = createContext<VerifyPage | null>(null);
const NO_LINK_NOTE = "Checked by the health worker or a medical officer on their verification page.";

/** OCR agreement shown apart from the human check (TrustPill) and from the clinician's acceptance (summary status). */
function MachineState({ f, caseId }: { f: Fact; caseId: string }) {
  const canVerify = useContext(VerifyAccess);
  const agreement = ocrAgreement(f);
  const link = f.trust === "unchecked" && canVerify ? verifyLink(caseId, f, canVerify) : null;
  if (!agreement && !link) return null;
  return (
    <p className="mt-1 flex flex-wrap items-center gap-2 text-xs">
      {agreement && (
        <span className={machineAgreed(f) ? "font-bold text-warning" : "text-muted"}>
          OCR engines: {agreement === "engines_agree" && f.trust !== "unchecked" ? "readings agreed" : AGREEMENT_WORDS[agreement]}
        </span>
      )}
      {link && <Link href={link} className="text-primary underline">Open in verification</Link>}
    </p>
  );
}

function FactItem({ f, caseId }: { f: Fact; caseId: string }) {
  const flags = f.flags.map(flagWords).filter((x): x is string => Boolean(x));
  return (
    <li id={`fact-${f.fact_id}`} className={`rounded-lg border p-2 ${f.trust === "unchecked" ? "border-dashed border-warning/60" : "border-subtle"}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p>
          <span className="text-muted">{f.label}: </span>
          <strong className="font-mono">{f.display}</strong>
        </p>
        <TrustPill trust={f.trust} />
      </div>
      <MachineState f={f} caseId={caseId} />
      {flags.map((w) => (
        <p key={w} className="mt-1 flex items-start gap-1 text-xs font-bold text-warning">
          <Icon name="alert" size={14} className="mt-0.5" /> {w}
        </p>
      ))}
      <Sources facts={[f]} />
    </li>
  );
}

function RowItem({ row, s, caseId }: { row: Row; s: EncounterSummary; caseId: string }) {
  const facts = factsFor(s, row.fact_ids);
  return (
    <li id={row.fact_ids[0] ? `fact-${row.fact_ids[0]}` : undefined} className={`rounded-lg border p-2 ${row.conflict ? "border-error/60 bg-error-bg" : "border-subtle"}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p>
          <span className="text-muted">{row.label}: </span>
          {row.conflict ? (
            <strong className="text-error">Sources disagree — no value chosen</strong>
          ) : (
            <strong className="font-mono">{row.display}</strong>
          )}
        </p>
        {!row.conflict && facts[0] && <TrustPill trust={facts[0].trust} />}
      </div>
      {row.conflict && (
        <ul className="mt-1 space-y-0.5 text-sm">
          {facts.map((f) => (
            <li key={f.fact_id} className="font-mono">
              {f.display} <span className="font-sans text-xs text-muted">({TRUST_WORDS[f.trust].label.toLowerCase()})</span>
            </li>
          ))}
        </ul>
      )}
      {!row.conflict && facts[0] && <MachineState f={facts[0]} caseId={caseId} />}
      {row.history.length > 0 && (
        <p className="mt-1 text-xs text-muted">Earlier in this visit (triage form): {row.history.map((h) => h.display).join(", ")}</p>
      )}
      <Sources facts={facts} />
    </li>
  );
}

function FactSection({ title, ids, s, caseId }: { title: string; ids: string[]; s: EncounterSummary; caseId: string }) {
  const facts = factsFor(s, ids);
  if (!facts.length) return null;
  return (
    <div className="space-y-1">
      <Eyebrow>{title}</Eyebrow>
      <ul className="space-y-1">{facts.map((f) => <FactItem key={f.fact_id} f={f} caseId={caseId} />)}</ul>
    </div>
  );
}

function RowSection({ title, rows, s, caseId }: { title: string; rows: Row[]; s: EncounterSummary; caseId: string }) {
  if (!rows.length) return null;
  return (
    <div className="space-y-1">
      <Eyebrow>{title}</Eyebrow>
      <ul className="space-y-1">{rows.map((r) => <RowItem key={r.key} row={r} s={s} caseId={caseId} />)}</ul>
    </div>
  );
}

const LINE_CLASS: Record<NoteLine["kind"], string> = {
  sentence: "", row: "", status: "", detail: "ml-4 text-muted", conflict: "font-bold text-error",
};

/** The whole summary as one note, in a fixed clinical order. Every line is the server's text; nothing is composed here. */
export function NoteDocument({ s, reviewText, stale }: { s: EncounterSummary; reviewText: string; stale: boolean }) {
  const sections = noteSections(s);
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  if (!sections.length) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(s.note_text ?? "");
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  }

  // Print only the note: a copy of it is placed in its own root and everything else is removed from the printed page
  // (globals.css, html.print-note), so no navigation, button or other case's content can reach the paper.
  function print() {
    const note = document.getElementById("encounter-note");
    if (!note) return;
    document.getElementById("print-root")?.remove();
    const root = document.createElement("div");
    root.id = "print-root";
    root.appendChild(note.cloneNode(true));
    root.querySelector("#encounter-note")?.removeAttribute("id");
    const foot = document.createElement("p");
    foot.className = "print-footer";
    foot.textContent = `Printed ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC · visit ${s.case_id} · ${reviewText}${stale ? " · OUT OF DATE" : ""} · ${s.disclaimer}`;
    root.appendChild(foot);
    document.body.appendChild(root);
    const html = document.documentElement;
    html.classList.add("print-note");
    const done = () => {
      html.classList.remove("print-note");
      root.remove();
    };
    window.addEventListener("afterprint", done, { once: true });
    window.print();
  }

  return (
    <section aria-labelledby="encounter-note-title" className="space-y-2">
      <div className="no-print flex flex-wrap items-center justify-between gap-2">
        <Eyebrow icon="list">Summary note (draft; not a diagnosis)</Eyebrow>
        <div className="flex flex-wrap items-center gap-2">
          <span role="status" className="text-xs text-muted">
            {copied === "done" ? "Copied" : copied === "failed" ? "Could not copy. Select the text instead." : ""}
          </span>
          <Button variant="secondary" onClick={copy} disabled={!s.note_text}>
            <Icon name="clipboard" size={16} /> Copy note
          </Button>
          <Button variant="secondary" onClick={print}>
            <Icon name="list" size={16} /> Print
          </Button>
        </div>
      </div>
      <article id="encounter-note" className="space-y-3 rounded-lg border border-subtle bg-card p-4 text-sm">
        <header className="border-b border-subtle pb-2">
          <h4 id="encounter-note-title" className="text-base font-bold">Encounter summary</h4>
          <p className="text-xs text-muted">
            Visit {s.case_id} · {reviewText}
            {stale ? " · OUT OF DATE: information changed after this note was made" : ""} · generated {s.generated_at.slice(0, 16).replace("T", " ")} UTC
          </p>
        </header>
        {sections.map((sec) => (
          <section key={sec.key} aria-label={sec.title}>
            <h5 className="text-xs font-bold uppercase tracking-wide text-muted">{sec.title}</h5>
            <ul className="mt-0.5 space-y-0.5">
              {sec.lines.map((line, i) => (
                <li key={i} className={LINE_CLASS[line.kind]}>
                  {line.fact_ids[0] && line.kind !== "detail" ? <a href={`#fact-${line.fact_ids[0]}`} className="hover:underline">{line.text}</a> : line.text}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </article>
      <p className="no-print text-xs text-muted">Select a line to jump to its value and sources below.</p>
    </section>
  );
}

/** Uploaded medical images. The described fields are model output for a clinician to check against the image itself. */
function ImagingSection({ ids, s }: { ids: string[]; s: EncounterSummary }) {
  const facts = factsFor(s, ids);
  if (!facts.length) return null;
  return (
    <div className="space-y-1">
      <Eyebrow>Imaging (AI-described visual findings; not a diagnosis)</Eyebrow>
      <ul className="space-y-1">
        {facts.map((f) => {
          const v = imageValue(f);
          if (!v) return null;
          const rows = imageFieldRows(v);
          return (
            <li key={f.fact_id} id={`fact-${f.fact_id}`} className={`space-y-1 rounded-lg border p-2 ${imageNeedsReview(v) ? "border-error/60 bg-error-bg" : f.trust === "unchecked" ? "border-dashed border-warning/60" : "border-subtle"}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p><strong>{f.label}</strong> <span className="text-muted">· {f.display.split(", ").slice(1).join(", ")}</span></p>
                <TrustPill trust={f.trust} />
              </div>
              {imageNeedsReview(v) && (
                <p className="flex items-start gap-1 text-sm font-bold text-error">
                  <Icon name="alert" size={14} className="mt-0.5" /> This image carries a flag that nobody has marked as reviewed. Sign-off is blocked until it is.
                </p>
              )}
              {imageDescribedBy(v) && (
                <p className={`text-xs ${imageDescribedBy(v)!.demo ? "rounded border border-warning bg-warning-bg px-2 py-1 font-bold" : "text-muted"}`}>{imageDescribedBy(v)!.text}</p>
              )}
              {rows.length > 0 && (
                <dl className="grid gap-x-3 gap-y-0.5 text-sm sm:grid-cols-[minmax(8rem,auto)_1fr]">
                  {rows.map((r) => (
                    <div key={r.label} className="contents">
                      <dt className="text-muted">{r.label}</dt>
                      <dd>{r.text}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {v.flags.map((x, i) => (
                <p key={i} className="text-xs font-bold text-warning">
                  Keyword flag for the reviewer: {(x.signal ?? "flag").replaceAll("_", " ").toLowerCase()}{x.negated ? " (negated in the text)" : ""}
                </p>
              ))}
              {v.text_withheld && <p className="text-xs text-warning">Some model text was withheld because it read as a diagnosis or an instruction.</p>}
              {v.note && <p className="text-xs text-muted">{v.note}</p>}
              <p className="text-xs">
                <a href={v.source_image_url} target="_blank" rel="noreferrer" className="text-primary underline">Open the image</a>
              </p>
              <Sources facts={[f]} />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const DOC_WORD: Record<string, string> = { lab_report: "Lab report", prescription: "Prescription", discharge_summary: "Discharge summary" };
const MATCH_WORD: Record<string, string> = { does_not_match: "marked as not this patient's", unsure: "marked not sure" };

/** Operational counts only — no confidence score. Three separate states are spelled out: OCR, person, clinician. */
function EvidenceStatusPanel({ st, reviewStatus }: { st: EvidenceStatus; reviewStatus: string }) {
  const d = st.documents, o = st.observations, m = st.summary;
  const ready = st.readiness.state === "ready_for_clinician_review";
  const items: [string, number | undefined][] = [
    ["Documents", d?.total], ["Read", d?.read], ["Still being read", d?.processing], ["Could not be read", d ? d.failed + d.quality_rejected : undefined],
    ["Not matched to the patient", d?.unmatched], ["Document values extracted", o?.extracted], ["Checked by a person", o?.human_checked],
    ["Machine-agreed, not yet checked", o?.machine_agreed_unchecked], ["Not yet checked (all)", o?.unchecked], ["Marked not sure", o?.unsure],
    ["Machine readings differ", o?.engines_disagree], ["Sentences in the written summary", m.written_sentences], ["Values left out of the text", m.values_withheld_from_text],
    ["Sentences blocked by the safety check", m.withheld_sentences], ["Conflicts", m.conflicts],
  ];
  return (
    <section aria-labelledby="evidence-status" className={`space-y-2 rounded-lg border p-3 ${ready ? "border-human" : "border-warning/60"}`}>
      <h4 id="evidence-status" className="font-bold">Evidence status</h4>
      <p className={`text-sm font-bold ${ready ? "text-human" : "text-warning"}`}>{READINESS_WORDS[st.readiness.state]}</p>
      {st.readiness.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-sm">{readinessReasonWords(st.readiness.reasons).map((r) => <li key={r}>{r}</li>)}</ul>
      )}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-3">
        {items.filter(([, n]) => typeof n === "number").map(([label, n]) => (
          <div key={label} className="flex justify-between gap-2 border-b border-subtle py-0.5">
            <dt className="text-muted">{label}</dt>
            <dd className="font-mono font-bold">{n}</dd>
          </div>
        ))}
      </dl>
      {!d && <p className="text-xs text-muted">Document counts are not shown: the documents could not be read for this summary.</p>}
      <p className="text-xs text-muted">
        Three separate states: <strong>OCR engines agree</strong> (machine cross-check) · <strong>checked by a person</strong> (against the paper or
        read-back) · <strong>clinician review of this summary</strong>: {reviewWords(reviewStatus).text.toLowerCase()}.
      </p>
      <p className="text-xs text-muted">{st.note}</p>
    </section>
  );
}

function WithheldSection({ items, s, caseId }: { items: Withheld[]; s: EncounterSummary; caseId: string }) {
  const canVerify = useContext(VerifyAccess);
  if (!items.length) return null;
  return (
    <section aria-labelledby="summary-withheld" className="space-y-1">
      <Eyebrow icon="eye">Withheld pending verification</Eyebrow>
      <p id="summary-withheld" className="text-xs text-muted">
        Not written into the drafted handover above, and why. Blocked sentences are never shown as text; the facts they would have used are listed.
        {!canVerify && ` ${NO_LINK_NOTE}`}
      </p>
      <ul className="space-y-1 text-sm">
        {groupWithheld(items).map((w, i) => {
          const facts = factsFor(s, w.fact_ids);
          return (
            <li key={i} className="rounded-lg border border-subtle p-2">
              <p className="font-bold">{withheldReasonWords(w.reason)}</p>
              <p className="text-xs text-muted">
                {w.kind === "sentence" ? "A sentence about" : w.kind === "document" ? "Values on a document, section" : "Section"}: {w.section.replaceAll("_", " ")}
                {w.kind === "fact" && ` · ${w.fact_ids.length} value${w.fact_ids.length === 1 ? "" : "s"}`}
              </p>
              {facts.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-xs">
                  {facts.map((f) => {
                    const link = f.trust === "unchecked" && canVerify ? verifyLink(caseId, f, canVerify) : null;
                    return (
                      <li key={f.fact_id}>
                        <a href={`#fact-${f.fact_id}`} className="underline">{f.label}</a> <span className="font-mono">{f.display}</span>{" "}
                        <span className="text-muted">({TRUST_WORDS[f.trust].label.toLowerCase()})</span>
                        {link && <> · <Link href={link} className="text-primary underline">Open in verification</Link></>}
                      </li>
                    );
                  })}
                </ul>
              )}
              {w.kind === "document" && w.document_id && (
                <p className="text-xs"><a href={`#unmatched-${w.document_id}`} className="text-primary underline">See the document below</a></p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function UnmatchedSection({ docs, caseId }: { docs: UnmatchedDocument[]; caseId: string }) {
  const canVerify = useContext(VerifyAccess);
  if (!docs.length) return null;
  return (
    <section aria-labelledby="unmatched-docs" className="space-y-2 rounded-lg border border-warning/60 p-3">
      <h4 id="unmatched-docs" className="font-bold">Documents awaiting patient matching</h4>
      <p className="text-xs">
        Nobody has said that these documents belong to this patient and this visit, so their values are <strong>not part of this summary</strong>.
        They are listed only so nothing is hidden. Nothing is ever matched automatically.
      </p>
      {docs.map((d) => (
        <div key={d.document_id} id={`unmatched-${d.document_id}`} className="space-y-1">
          <p className="text-sm">
            <strong>{DOC_WORD[d.document_type] ?? d.document_type}</strong>
            <span className="text-muted">
              {" "}· {d.attestation ? MATCH_WORD[d.attestation] ?? d.attestation : "not answered yet"}
              {d.dates?.report_date ? ` · reported ${d.dates.report_date}` : ""}{d.dates?.collected_date ? ` · collected ${d.dates.collected_date}` : ""}
            </span>
          </p>
          <ul className="space-y-0.5 text-xs">
            {d.rows.map((r) => (
              <li key={r.field_id} className="flex flex-wrap justify-between gap-2 border-b border-dashed border-warning/40 py-0.5">
                <span>{r.label} <span className="font-mono">{r.shown}</span>{r.printed_flag ? ` · printed flag ${r.printed_flag}` : ""}</span>
                <span className="text-muted">Unmatched · {AGREEMENT_WORDS[r.ocr_agreement]} · page {r.page_index + 1}</span>
              </li>
            ))}
          </ul>
          {canVerify ? (
            <Link href={documentLink(caseId, d.document_id, canVerify)} className="text-xs text-primary underline">
              Answer &quot;Is this the patient&apos;s report?&quot;
            </Link>
          ) : (
            <p className="text-xs text-muted">The health worker or a medical officer answers &quot;Is this the patient&apos;s report?&quot; on their verification page.</p>
          )}
        </div>
      ))}
    </section>
  );
}

export function EncounterSummaryPanel({ caseId, canReview }: { caseId: string; canReview: boolean }) {
  const [view, setView] = useState<SummaryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ack, setAck] = useState(false);
  const [canVerify, setCanVerify] = useState<VerifyPage | null>(null);
  useEffect(() => {
    api.get<{ role: string }>("auth/me").then((me) => setCanVerify(verificationBase(me.role)), () => setCanVerify(null));
  }, []);

  // A reviewer who opens a case with no summary gets one without pressing a button (once per case; never for a reader).
  const autoTried = useRef<string | null>(null);
  const load = useCallback(async () => {
    try {
      let got = await api.get<SummaryView>(`cases/${caseId}/encounter-summary`);
      if (shouldAutoBuild(got, canReview, autoTried.current === caseId)) {
        autoTried.current = caseId;
        got = { ...(await api.post<SummaryView>(`cases/${caseId}/encounter-summary`)), case_id: caseId };
      }
      setView(got);
      setError(null);
    } catch (err) {
      setError(explain(err));
    }
  }, [caseId, canReview]);

  useEffect(() => {
    setView(null);
    setAck(false);
    void load();
  }, [load]);

  // Evidence recorded elsewhere on this screen (an image marked as reviewed): re-read, so a stale summary says so.
  useEffect(() => {
    const onChange = (e: Event) => {
      if ((e as CustomEvent<{ caseId?: string }>).detail?.caseId === caseId) void load();
    };
    window.addEventListener("sehat:evidence-changed", onChange);
    return () => window.removeEventListener("sehat:evidence-changed", onChange);
  }, [caseId, load]);

  async function generate() {
    setBusy(true);
    try {
      setView({ ...(await api.post<SummaryView>(`cases/${caseId}/encounter-summary`)), case_id: caseId });
      setAck(false);
      setError(null);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function decide(outcome: "accepted" | "needs_changes") {
    if (!view?.summary_id) return;
    setBusy(true);
    try {
      const body = { outcome, acknowledged_conflicts: ack, supersedes: view.review?.latest?.event_id ?? null };
      setView(await api.post<SummaryView>(`cases/${caseId}/encounter-summary/${view.summary_id}/review`, body));
      setError(null);
    } catch (err) {
      setError(explain(err));
      await load();
    } finally {
      setBusy(false);
    }
  }

  const s = view?.summary ?? null;
  const status = reviewWords(view?.review?.status);
  const accept = view ? acceptState(view) : null;

  return (
    <VerifyAccess.Provider value={canVerify}>
    <SectionCard
      id="encounter-summary-heading"
      title="Encounter summary"
      icon="clipboard"
      aside={s ? <StatusPill tone={status.tone} icon={status.tone === "done" ? "check" : "eye"}>{status.text}</StatusPill> : undefined}
    >
      {error && <Notice tone="error" role="alert">{error}</Notice>}
      {!view && !error && <Spinner label="Loading the encounter summary" />}

      {view && !s && (
        <div className="space-y-2">
          <p className="text-sm">{view.message ?? "No encounter summary yet."}</p>
          {canReview && (
            <Button onClick={generate} disabled={busy}>
              <Icon name="refresh" size={16} /> Build summary from all recorded sources
            </Button>
          )}
        </div>
      )}

      {s && (
        <div className="space-y-4">
          {view?.is_stale && (
            <Notice tone="warning" role="alert" title="Out of date">
              <p>{view.stale_message}</p>
              {canReview && (
                <Button variant="secondary" onClick={generate} disabled={busy} className="mt-1">
                  <Icon name="refresh" size={16} /> Refresh summary
                </Button>
              )}
            </Notice>
          )}

          {s.attention.length > 0 && (
            <section aria-labelledby="summary-attention" className="space-y-1 rounded-lg border border-warning/60 bg-warning-bg p-3">
              <h4 id="summary-attention" className="flex items-center gap-1 font-bold">
                <Icon name="alert" size={16} /> Needs attention before relying on this summary
              </h4>
              <ul className="space-y-1.5">
                {sortedAttention(s.attention).map((a, i) => (
                  <li key={i} className="text-sm">
                    <p className={`font-bold ${attentionTone(a.kind) === "danger" ? "text-error" : ""}`}>{a.message}</p>
                    <p>{a.action}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {s.evidence_status && <EvidenceStatusPanel st={s.evidence_status} reviewStatus={view?.review?.status ?? "draft_pending_review"} />}

          {/* Urgency stays the rules engine's and is shown apart from the drafted text. */}
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-subtle p-2">
            <Eyebrow icon="scale">Urgency</Eyebrow>
            {s.urgency.recorded_urgency ? <UrgencyBadge urgency={s.urgency.recorded_urgency} size="sm" /> : <strong>Not determined</strong>}
            <ProvenanceBadge kind="rules_engine" />
            <p className="basis-full text-xs text-muted">{s.urgency.message}</p>
          </div>

          <NoteDocument s={s} reviewText={status.text} stale={Boolean(view?.is_stale)} />

          {noteSections(s).length === 0 && s.narrative.length > 0 && (
            <div className="space-y-1">
              <Eyebrow icon="list">Drafted handover (from the facts below; not a diagnosis)</Eyebrow>
              <ul className="list-disc space-y-0.5 pl-5 text-sm">
                {s.narrative.map((n, i) => <li key={i}>{n.text}</li>)}
              </ul>
              {s.blocked.length > 0 && (
                <p className="text-xs font-bold text-warning">
                  {s.blocked.length} sentence(s) withheld by the safety checks; their facts are still listed below.
                </p>
              )}
            </div>
          )}

          {noteSections(s).length === 0 && s.narrative.length === 0 && <p className="text-sm text-muted">No sentence could be written yet: every value is still unchecked, conflicting or missing.</p>}
          <WithheldSection items={s.withheld ?? []} s={s} caseId={caseId} />
          <UnmatchedSection docs={s.unmatched_documents ?? []} caseId={caseId} />

          <FactSection title="Presenting problem" ids={s.presenting_problem} s={s} caseId={caseId} />
          <FactSection title="Duration and onset (as reported)" ids={s.timeline} s={s} caseId={caseId} />
          <FactSection title="Symptoms reported" ids={s.symptoms.present} s={s} caseId={caseId} />
          <FactSection title="Symptoms denied" ids={s.symptoms.denied} s={s} caseId={caseId} />
          <FactSection title="Interview answers (as said; confirmed by the health worker; not used as measurements)" ids={s.interview ?? []} s={s} caseId={caseId} />
          <RowSection title="Measurements" rows={s.vitals} s={s} caseId={caseId} />
          <RowSection title="Lab values (with report date)" rows={s.labs} s={s} caseId={caseId} />
          <ImagingSection ids={s.imaging ?? []} s={s} />
          <FactSection title="Medicines (on a prescription or mentioned; not proof they were taken)" ids={s.medications} s={s} caseId={caseId} />
          <FactSection title="Red flags" ids={s.red_flag_mentions} s={s} caseId={caseId} />
          <FactSection title="Body map" ids={s.body_map} s={s} caseId={caseId} />
          <FactSection title="Other context" ids={s.context} s={s} caseId={caseId} />

          {s.missing_information.length > 0 && (
            <div className="space-y-1">
              <Eyebrow icon="question">Missing — needs human review (never treated as normal)</Eyebrow>
              <ul className="space-y-1 text-sm">
                {s.missing_information.map((m) => (
                  <li key={m.key} className="rounded-lg border border-warning/40 p-2">
                    <strong>{m.label}</strong>
                    {m.is_danger_sign && <span className="ml-1 text-xs font-bold text-error">danger-sign screen</span>}
                    <p className="text-xs text-muted">{m.why_it_matters}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {s.pending_review.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer font-bold">Waiting for a person to check ({s.pending_review.length})</summary>
              <ul className="mt-1 space-y-1 pl-3">
                {s.pending_review.map((p) => (
                  <li key={`${p.kind}:${p.ref}`}>
                    <strong>{p.label.replaceAll("_", " ")}</strong> — {p.action}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {s.excluded_sources.length > 0 && (
            <p className="text-xs text-muted">Not included by consent choice: {s.excluded_sources.map((x) => sourceName(x.source)).join(", ")}.</p>
          )}

          <details className="text-xs">
            <summary className="cursor-pointer">What the labels mean</summary>
            <ul className="mt-1 space-y-0.5 pl-3">
              {(Object.keys(TRUST_WORDS) as Trust[]).map((t) => (
                <li key={t}>
                  <strong>{TRUST_WORDS[t].label}:</strong> {s.trust_legend[t]}
                </li>
              ))}
            </ul>
          </details>

          {canReview && accept && (
            <div className="space-y-2 border-t border-subtle pt-3" role="group" aria-label="Summary review">
              {accept.needsConflictAck && (
                <label className="flex items-start gap-2 text-sm">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />
                  I have seen the conflicting values above. Accepting the summary does not choose a value; record the right one on the triage form.
                </label>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="success" disabled={busy || !accept.allowed || (accept.needsConflictAck && !ack)} onClick={() => decide("accepted")}>
                  <Icon name="check" size={16} /> Accept summary
                </Button>
                <Button variant="secondary" disabled={busy || !view?.summary_id} onClick={() => decide("needs_changes")}>
                  <Icon name="pencil" size={16} /> Needs changes
                </Button>
                {!view?.is_stale && (
                  <Button variant="quiet" disabled={busy} onClick={generate}>
                    <Icon name="refresh" size={16} /> Rebuild
                  </Button>
                )}
              </div>
              {accept.reason && <p className="text-xs text-muted">{accept.reason}</p>}
              <p className="text-xs text-muted">Accepting records your review of this summary only. It does not sign off the case or change urgency.</p>
            </div>
          )}

          <p className="text-xs text-muted">{s.disclaimer}</p>
        </div>
      )}
    </SectionCard>
    </VerifyAccess.Provider>
  );
}
