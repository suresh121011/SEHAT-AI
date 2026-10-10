"use client";

// Clinical encounter summary (backend app/ai/encounter_summary.py, docs/19). One case-level, source-linked view of
// everything recorded: needs-attention first, the rules engine's urgency kept apart from the drafted text, then each
// section as rows with a trust level and their sources. Nothing here computes urgency or picks between conflicting
// values; accepting the summary changes nothing in triage. Not a diagnosis.
import { useCallback, useEffect, useState } from "react";

import { Icon } from "@/components/Icon";
import { ProvenanceBadge, StatusPill, UrgencyBadge } from "@/components/review/Badges";
import { Eyebrow, SectionCard } from "@/components/review/Panel";
import { Button, Notice, Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  TRUST_WORDS, acceptState, attentionTone, factsFor, flagWords, reviewWords, sortedAttention, sourceName, sourceWords,
  type EncounterSummary, type Fact, type Row, type SummaryView, type Trust,
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

function FactItem({ f }: { f: Fact }) {
  const flags = f.flags.map(flagWords).filter((x): x is string => Boolean(x));
  return (
    <li className={`rounded-lg border p-2 ${f.trust === "unchecked" ? "border-dashed border-warning/60" : "border-subtle"}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p>
          <span className="text-muted">{f.label}: </span>
          <strong className="font-mono">{f.display}</strong>
        </p>
        <TrustPill trust={f.trust} />
      </div>
      {flags.map((w) => (
        <p key={w} className="mt-1 flex items-start gap-1 text-xs font-bold text-warning">
          <Icon name="alert" size={14} className="mt-0.5" /> {w}
        </p>
      ))}
      <Sources facts={[f]} />
    </li>
  );
}

function RowItem({ row, s }: { row: Row; s: EncounterSummary }) {
  const facts = factsFor(s, row.fact_ids);
  return (
    <li className={`rounded-lg border p-2 ${row.conflict ? "border-error/60 bg-error-bg" : "border-subtle"}`}>
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
      {row.history.length > 0 && (
        <p className="mt-1 text-xs text-muted">Earlier triage entries: {row.history.map((h) => h.display).join(", ")}</p>
      )}
      <Sources facts={facts} />
    </li>
  );
}

function FactSection({ title, ids, s }: { title: string; ids: string[]; s: EncounterSummary }) {
  const facts = factsFor(s, ids);
  if (!facts.length) return null;
  return (
    <div className="space-y-1">
      <Eyebrow>{title}</Eyebrow>
      <ul className="space-y-1">{facts.map((f) => <FactItem key={f.fact_id} f={f} />)}</ul>
    </div>
  );
}

function RowSection({ title, rows, s }: { title: string; rows: Row[]; s: EncounterSummary }) {
  if (!rows.length) return null;
  return (
    <div className="space-y-1">
      <Eyebrow>{title}</Eyebrow>
      <ul className="space-y-1">{rows.map((r) => <RowItem key={r.key} row={r} s={s} />)}</ul>
    </div>
  );
}

export function EncounterSummaryPanel({ caseId, canReview }: { caseId: string; canReview: boolean }) {
  const [view, setView] = useState<SummaryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ack, setAck] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await api.get<SummaryView>(`cases/${caseId}/encounter-summary`));
      setError(null);
    } catch (err) {
      setError(explain(err));
    }
  }, [caseId]);

  useEffect(() => {
    setView(null);
    setAck(false);
    void load();
  }, [load]);

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

          {/* Urgency stays the rules engine's and is shown apart from the drafted text. */}
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-subtle p-2">
            <Eyebrow icon="scale">Urgency</Eyebrow>
            {s.urgency.recorded_urgency ? <UrgencyBadge urgency={s.urgency.recorded_urgency} size="sm" /> : <strong>Not determined</strong>}
            <ProvenanceBadge kind="rules_engine" />
            <p className="basis-full text-xs text-muted">{s.urgency.message}</p>
          </div>

          {s.narrative.length > 0 && (
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

          <FactSection title="Presenting problem" ids={s.presenting_problem} s={s} />
          <FactSection title="Duration and onset (as reported)" ids={s.timeline} s={s} />
          <FactSection title="Symptoms reported" ids={s.symptoms.present} s={s} />
          <FactSection title="Symptoms denied" ids={s.symptoms.denied} s={s} />
          <RowSection title="Measurements" rows={s.vitals} s={s} />
          <RowSection title="Lab values (with report date)" rows={s.labs} s={s} />
          <FactSection title="Medicines (on a prescription or mentioned; not proof they were taken)" ids={s.medications} s={s} />
          <FactSection title="Red flags" ids={s.red_flag_mentions} s={s} />
          <FactSection title="Body map" ids={s.body_map} s={s} />
          <FactSection title="Other context" ids={s.context} s={s} />

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
  );
}
