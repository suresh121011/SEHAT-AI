"use client";

// Central case-review workspace. Shows the rules engine's own result for the latest triage run (app/review_queue.py
// GET /triage/{case_id}); nothing here computes or changes urgency. Clinical content is shown only while triage
// consent is in effect; the urgency and RED status stay visible regardless, for safety.
import { useState } from "react";

import { Icon } from "@/components/Icon";
import { ProvenanceBadge, StatusPill, URGENCY_MEANING, UrgencyBadge } from "@/components/review/Badges";
import { AcknowledgeButton, CorrectVitalsDialog, OverrideDialog, SignOffDialog, type ActionDone } from "@/components/review/ReviewActions";
import { Eyebrow, SectionCard } from "@/components/review/Panel";
import { Button, Notice } from "@/components/ui";
import { SCENARIO_WORDS, correctionLine, escalationView, fieldWords, formatClock, priorityExplanation, provenanceNote, roleLabel, type CaseReview, type FieldProvenance, type ReviewEvent } from "@/lib/review";

type Dialog = "sign_off" | "override" | "correct" | null;

const DETERMINATION: Record<string, { title: string; text: string }> = {
  complete: { title: "Rules had the inputs they need", text: "The result still needs your review." },
  insufficient_data: { title: "Needs information", text: "Some inputs are missing, so the rules gave at least YELLOW and flagged human review. Missing values are never treated as normal." },
  outside_validated_population: { title: "Outside the rules’ population", text: "This patient is outside the group the rules were written for. Use clinical judgement; the result needs human review." },
};

type Props = {
  review: CaseReview;
  now: number;
  offsetMs: number;
  onAction: (d: ActionDone) => void;
  onStale: () => void;
  onDialogChange: (open: boolean) => void;
  /** Medical images whose AI findings still need "Findings reviewed" (from the evidence panel); blocks sign-off. */
  imageAcksPending?: number;
};

export function CaseWorkspace({ review, now, offsetMs, onAction, onStale, onDialogChange, imageAcksPending = 0 }: Props) {
  const [dialog, setDialogState] = useState<Dialog>(null);
  const setDialog = (d: Dialog) => {
    setDialogState(d);
    onDialogChange(d !== null);
  };
  const latest = review.latest;
  const c = review.case;

  if (!latest) {
    return (
      <div className="space-y-3 p-4">
        <CaseTitle review={review} />
        <Notice tone="info" title="No triage run recorded yet">
          <p>This case has no rules-engine result to review. It appears in the queue once a health worker submits triage.</p>
        </Notice>
      </div>
    );
  }

  const result = latest.result;
  const esc = latest.escalation;
  const escView = esc ? escalationView(esc, now, offsetMs) : null;
  const signedOff = latest.review_status === "signed_off";
  const lowered = latest.effective_urgency !== latest.priority_urgency;
  const raised = latest.priority_source === "reviewer_override_raise";
  const canAct = review.can_review && review.clinical_content_available;
  const det = DETERMINATION[latest.determination ?? ""] ?? null;
  const runEvents = review.review_events.filter((e) => e.triage_run_id === latest.triage_run_id);

  return (
    <div className="space-y-4 p-4">
      <CaseTitle review={review} />

      {/* Decision block: rules result first, reviewer-recorded urgency beside it, queue position explained. */}
      <section aria-labelledby="decision-heading" className="rounded-xl border border-subtle bg-card shadow-card">
        <h3 id="decision-heading" className="sr-only">
          Current decision
        </h3>
        {/* Two sources, two surfaces: the rules result on white with a solid rules badge; the reviewer's record on the
            human tint with the "Human reviewer" badge. Same words and icons as the provenance badges elsewhere. */}
        <div className="grid overflow-hidden rounded-t-lg sm:grid-cols-2">
          <div className="space-y-2 border-b border-subtle bg-card p-4 sm:border-r sm:border-b-0">
            <Eyebrow icon="scale">Rules-engine result</Eyebrow>
            <UrgencyBadge urgency={latest.rules_urgency} size="lg" />
            <p className="text-sm leading-6">{URGENCY_MEANING[latest.rules_urgency]}. Decided by fixed rules, not by AI.</p>
          </div>
          <div className="space-y-2 bg-human-bg/50 p-4">
            <Eyebrow icon="people">Reviewer-recorded urgency</Eyebrow>
            {latest.effective_urgency_source === "reviewer_override" ? (
              <>
                <UrgencyBadge urgency={latest.effective_urgency} size="lg" />
                <p className="text-sm leading-6">
                  Override on this run ({latest.override_count}). See history for the reason. <ProvenanceBadge kind="reviewer_action" />
                </p>
              </>
            ) : (
              <p className="text-sm leading-6 text-muted">No override yet. The rules result is provisional until a medical officer reviews and signs it off.</p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-subtle px-4 py-2 text-sm">
          {signedOff ? (
            <StatusPill tone="done" icon="check">
              Signed off (reviewed, not a diagnosis) {latest.signed_off_at ? new Date(latest.signed_off_at).toLocaleTimeString() : ""}
            </StatusPill>
          ) : (
            <StatusPill tone="neutral" icon="eye">
              Awaiting medical-officer review
            </StatusPill>
          )}
          {latest.priority_source === "open_red_escalation" ? (
            <span className="font-bold text-ink">{priorityExplanation(latest)}</span>
          ) : (
            <span className="text-muted">
              Queue position: <strong className="text-ink">{latest.priority_urgency}</strong>
              {raised ? " (raised by reviewer)" : lowered ? " (rules; a lowered urgency never moves a case down)" : " (rules)"}
            </span>
          )}
        </div>
      </section>

      {esc && escView && (
        <section aria-labelledby="case-esc" className={`space-y-2 rounded-lg border-2 p-4 ${escView.display === "acknowledged" ? "border-esc-ack-line bg-esc-ack text-esc-ack-ink" : escView.display === "pending" ? "border-esc-pending-line bg-esc-pending text-esc-pending-ink" : "border-esc-overdue bg-esc-overdue text-esc-overdue-ink"}`}>
          <h3 id="case-esc" className="flex flex-wrap items-center gap-2 font-bold tabular-nums">
            <Icon name={escView.display === "acknowledged" ? "check" : escView.display === "pending" ? "clock" : "alert"} size={20} />
            {escView.display === "acknowledged" && `RED acknowledged ${esc.acknowledged_at ? new Date(esc.acknowledged_at).toLocaleTimeString() : ""} by ${roleLabel(esc.acknowledged_by_role)} via ${esc.acknowledged_via === "sign_off" ? "sign-off" : "acknowledgment"}${esc.acknowledged_late ? " — after the target" : " — within the target"}`}
            {escView.display === "pending" && <>RED: acknowledge within <span className="font-mono">{formatClock(escView.remainingMs)}</span></>}
            {escView.display === "past_target" && <>RED: past the {esc.window_seconds / 60}-minute target by <span className="font-mono">{formatClock(escView.remainingMs)}</span> (server confirms on next refresh)</>}
            {escView.display === "overdue" && <>RED: OVERDUE by <span className="font-mono">{formatClock(escView.remainingMs)}</span> (server-confirmed)</>}
          </h3>
          <p className="text-sm">
            Server deadline {new Date(esc.deadline).toLocaleTimeString()}
            {esc.anchor_source === "earlier_red_run" && " (clock started at an earlier RED run; re-running triage does not reset it)"}
            {esc.anchor_source === "reviewer_override" && " (clock started when a reviewer raised the case to RED)"}. No SMS, call or alert is sent by this prototype; it does not replace your facility’s usual escalation.
          </p>
          {escView.display !== "acknowledged" && review.can_review && <AcknowledgeButton caseId={c.case_id} latest={latest} onDone={onAction} onStale={onStale} />}
          {escView.display !== "acknowledged" && !review.can_review && <p className="text-sm">Only a medical officer can acknowledge.</p>}
        </section>
      )}

      {!review.clinical_content_available && (
        <Notice tone="consent" title="Triage consent is not in effect">
          <p>Clinical details are hidden and no review can be recorded. The urgency and RED status stay visible for safety.</p>
        </Notice>
      )}

      {/* Actions */}
      <SectionCard id="actions-heading" title="Reviewer actions" icon="people">
        {!review.can_review ? (
          <p className="text-sm text-muted">Read only. Supervisors can view a case but only a medical officer can sign off, override or acknowledge.</p>
        ) : !canAct ? (
          <p className="text-sm text-muted">No action can be recorded without triage consent.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setDialog("sign_off")} disabled={signedOff || imageAcksPending > 0}>
              <Icon name="check" size={16} /> {signedOff ? "Signed off" : "Sign off review…"}
            </Button>
            <Button variant="danger" onClick={() => setDialog("override")} disabled={signedOff}>
              <Icon name="flag" size={16} /> Override urgency…
            </Button>
            <Button variant="secondary" onClick={() => setDialog("correct")} disabled={!latest.input}>
              <Icon name="pencil" size={16} /> Correct vitals…
            </Button>
            {signedOff && <p className="basis-full text-xs text-muted">This run is signed off. A corrected re-run starts a new review.</p>}
            {!signedOff && imageAcksPending > 0 && <ImageAckLine count={imageAcksPending} />}
            {!latest.input && <p className="basis-full text-xs text-muted">Correct vitals is unavailable: this run was recorded before inputs were stored.</p>}
          </div>
        )}
      </SectionCard>

      {result && det && (
        <SectionCard id="why-heading" title={`Why the rules gave ${result.urgency}`} icon="scale" aside={<ProvenanceBadge kind="rules_engine" detail={`engine ${result.engine_version} · rules ${result.ruleset_version}`} />}>
          <div className={`rounded-lg border-l-4 px-3 py-2 ${latest.determination === "complete" ? "border-primary bg-primary-tint/40" : "border-warning bg-warning-bg"}`}>
            <p className="font-bold">{det.title}</p>
            <p className="text-sm">{det.text}</p>
          </div>
          {result.triggered_rules.length === 0 ? (
            <p className="text-sm text-muted">No rule raised the urgency.</p>
          ) : (
            <ol className="space-y-2">
              {result.triggered_rules.map((r) => (
                <li key={r.rule_id} className="rounded-lg border border-subtle p-2">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="font-bold">{r.reason}</p>
                    <span className="whitespace-nowrap rounded-lg border border-line px-1.5 text-xs font-bold leading-5">Rule level {r.urgency}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted">
                    <span className="font-mono">{r.rule_id}</span> · Source: {r.source || "Source unavailable"}
                  </p>
                </li>
              ))}
            </ol>
          )}
          {result.missing_fields.length > 0 && (
            <div className="rounded-lg border border-warning/50 bg-warning-bg p-2">
              <p className="flex items-center gap-1 font-bold text-warning">
                <Icon name="question" size={16} /> Missing — needs human review
              </p>
              <ul className="mt-1 flex flex-wrap gap-1">
                {result.missing_fields.map((f) => (
                  <li key={f} className="rounded-lg border border-warning/40 bg-card px-2 py-0.5 text-sm">
                    {fieldWords(f)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.advisories.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {result.advisories.map((a) => (
                <li key={a.code}>{a.message}</li>
              ))}
            </ul>
          )}
        </SectionCard>
      )}

      {review.clinical_content_available && <RecordedInput input={latest.input ?? null} provenance={latest.field_provenance} />}

      <History review={review} runEvents={runEvents} />

      {/* One set of dialogs per workspace; polling pauses while one is open (onDialogChange). */}
      <CaseDialogs review={review} dialog={dialog} setDialog={setDialog} onAction={onAction} onStale={onStale} />
    </div>
  );
}

function ImageAckLine({ count }: { count: number }) {
  return (
    <p className="flex basis-full items-start gap-1 text-sm font-bold text-warning">
      <Icon name="alert" size={16} className="mt-0.5" />
      Sign-off waits until you mark the AI image findings as reviewed ({count} image{count === 1 ? "" : "s"}) under Source evidence → Medical image findings.
    </p>
  );
}

function CaseDialogs({ review, dialog, setDialog, onAction, onStale }: { review: CaseReview; dialog: Dialog; setDialog: (d: Dialog) => void; onAction: (d: ActionDone) => void; onStale: () => void }) {
  if (!review.latest) return null;
  return (
    <>
      <SignOffDialog open={dialog === "sign_off"} onClose={() => setDialog(null)} caseId={review.case.case_id} latest={review.latest} onDone={onAction} onStale={onStale} />
      <OverrideDialog open={dialog === "override"} onClose={() => setDialog(null)} caseId={review.case.case_id} latest={review.latest} reasons={review.override_reasons} onDone={onAction} onStale={onStale} />
      {review.latest.input && (
        // Not keyed by run: after a stale reload the dialog keeps its message and resets its values to the new run.
        <CorrectVitalsDialog
          open={dialog === "correct"}
          onClose={() => setDialog(null)}
          caseId={review.case.case_id}
          latest={review.latest}
          reasons={review.correction_reasons ?? []}
          onDone={onAction}
          onStale={onStale}
        />
      )}
    </>
  );
}

function CaseTitle({ review }: { review: CaseReview }) {
  const c = review.case;
  const l = review.latest;
  return (
    <header className="space-y-1">
      <h2 className="text-2xl font-bold">
        Case {c.patient_token}
      </h2>
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted">
        <span>{SCENARIO_WORDS[c.scenario] ?? c.scenario}</span>
        <span>Facility {c.facility_code}</span>
        {l && <span>Triaged {new Date(l.triaged_at).toLocaleString()}</span>}
        {l && <span className="font-mono">Run {l.triage_run_id.slice(0, 8)}</span>}
        <span>Pseudonymous token; no name is stored.</span>
      </p>
    </header>
  );
}

const VITAL_ROWS: [string, string, string][] = [
  ["spo2", "Oxygen saturation", "%"],
  ["pulse", "Pulse", "/min"],
  ["resp_rate", "Breathing rate", "/min"],
  ["sbp", "Systolic BP", "mmHg"],
  ["dbp", "Diastolic BP", "mmHg"],
  ["temp_c", "Temperature", "°C"],
  ["consciousness", "Alertness (ACVPU)", ""],
  ["on_supplemental_oxygen", "On oxygen", ""],
];

function RecordedInput({ input, provenance }: { input: Record<string, unknown> | null; provenance: FieldProvenance | undefined }) {
  if (!input) {
    return (
      <SectionCard id="input-heading" title="Recorded triage input" icon="clipboard">
        <p className="text-sm text-muted">Source unavailable: this run was recorded before inputs were stored.</p>
      </SectionCard>
    );
  }
  const v = (input.vitals ?? {}) as Record<string, unknown>;
  const flags = (input.red_flags_present ?? []) as string[];
  const isCorrection = !!provenance && Object.keys(provenance).length > 0;
  const note = (path: string) => provenanceNote(path, provenance);
  const fmt = (x: unknown, unit: string) => (x === null || x === undefined ? null : typeof x === "boolean" ? (x ? "Yes" : "No") : `${x}${unit ? ` ${unit}` : ""}`);
  return (
    <SectionCard id="input-heading" title="Recorded triage input" icon="clipboard" aside={<ProvenanceBadge kind="recorded_input" detail="as submitted to the rules engine" />}>
      {isCorrection ? (
        <p className="text-xs text-muted">
          This run is a reviewer correction. Changed values say who changed them and why; every other value was carried over unchanged from the previous run. Who
          originally measured each value is not stored; see Evidence for sources.
        </p>
      ) : (
        <p className="text-xs text-muted">Entered or confirmed on the triage form by a health worker or doctor. Who measured each value is not stored with the run; see Evidence for sources.</p>
      )}
      <dl className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <Row label="Age" value={fmt(input.age_years, "years")} note={note("age_years")} />
        <Row label="Pregnant" value={fmt(input.pregnant, "")} note={note("pregnant")} />
        {VITAL_ROWS.map(([k, label, unit]) => (
          <Row key={k} label={label} value={fmt(v[k], unit)} note={note(`vitals.${k}`)} />
        ))}
        <Row label="Red-flag screen" value={input.red_flag_screen_completed ? "Completed" : null} missingText="Not completed — never inferred" note={note("red_flag_screen_completed")} />
        <Row label="Red flags recorded" value={flags.length ? flags.map((f) => f.replaceAll("_", " ")).join(", ") : input.red_flag_screen_completed ? "None" : null} note={note("red_flags_present")} />
      </dl>
    </SectionCard>
  );
}

function Row({ label, value, missingText = "Not recorded", note = null }: { label: string; value: string | null; missingText?: string; note?: ReturnType<typeof provenanceNote> }) {
  return (
    <div className={`grid grid-cols-[1fr_auto] gap-x-2 border-b border-subtle py-1.5 text-sm ${note?.kind === "corrected" ? "border-l-4 border-l-human pl-2" : ""}`}>
      <dt className="text-muted">{label}</dt>
      <dd className={`text-right tabular-nums ${value === null ? "italic text-warning" : "font-bold"}`}>{value ?? missingText}</dd>
      {note && <dd className={`col-span-2 text-xs ${note.kind === "corrected" ? "font-bold text-ink" : "text-muted"}`}>{note.text}</dd>}
    </div>
  );
}

const ACTION_WORDS: Record<string, string> = {
  case_created: "Case created",
  consent_recorded: "Consent recorded",
  consent_withdrawn: "Consent withdrawn",
  triage_run: "Rules engine run",
  triage_run_recorded: "Rules engine run",
  review_signed_off: "Review signed off",
  urgency_overridden: "Urgency overridden",
  red_escalation_acknowledged: "RED acknowledged",
  red_escalation_overdue: "RED passed acknowledgment target",
  ai_extraction_recorded: "AI extraction recorded",
  ai_field_reviewed: "AI field reviewed",
  ai_note_drafted: "AI note drafted",
};

function eventLine(e: ReviewEvent): string {
  if (e.kind === "sign_off") return "Signed off";
  if (e.kind === "acknowledge") return "RED acknowledged (seen)";
  return `Override ${e.old_urgency} → ${e.new_urgency}: ${e.reason_label ?? e.reason_code}`;
}

function History({ review, runEvents }: { review: CaseReview; runEvents: ReviewEvent[] }) {
  type Item = { at: string; key: string; icon: "scale" | "check" | "flag" | "eye" | "pencil"; title: string; who: string; detail?: string | null; current: boolean };
  const latestRun = review.latest?.triage_run_id;
  const items: Item[] = [
    ...review.runs.map((r) => ({
      at: r.created_at,
      key: `run-${r.run_id}`,
      icon: "scale" as const,
      title: `Rules engine: ${r.urgency}`,
      who: "Rules engine",
      detail: `run ${r.run_id.slice(0, 8)}${r.run_id === latestRun ? " (latest)" : " (superseded)"}${r.corrects_run_id ? ` · re-run on corrected input of run ${r.corrects_run_id.slice(0, 8)}` : ""}`,
      current: r.run_id === latestRun,
    })),
    ...(review.corrections ?? []).map((c) => ({
      at: c.created_at,
      key: `corr-${c.triage_run_id}`,
      icon: "pencil" as const,
      title: correctionLine(c),
      who: `${roleLabel(c.actor_role)}${c.is_current_user ? " (you)" : ""}`,
      detail: `Rules result ${c.urgency_before ?? "unknown"} → ${c.urgency_after}${c.reason_text ? ` · ${c.reason_text}` : ""}${c.changes === null ? " · values hidden (triage consent not in effect)" : ""}`,
      current: c.triage_run_id === latestRun,
    })),
    ...review.review_events.map((e) => ({
      at: e.created_at,
      key: e.event_id,
      icon: (e.kind === "sign_off" ? "check" : e.kind === "override" ? "flag" : "eye") as Item["icon"],
      title: eventLine(e),
      who: `${roleLabel(e.actor_role)}${e.is_current_user ? " (you)" : ""}`,
      detail: e.reason_text,
      current: e.triage_run_id === latestRun,
    })),
  ].sort((a, b) => (a.at < b.at ? 1 : -1));
  return (
    <SectionCard id="history-heading" title="Review history" icon="history" aside={<span className="text-xs text-muted">Append-only · newest first · server time</span>}>
      {runEvents.length === 0 && <p className="text-sm text-muted">No review action on the latest run yet.</p>}
      {/* Readable timeline: time column, a marker on a rail, then what happened and who. Earlier runs are labelled
          in words (not faded), so their text keeps full contrast. */}
      <ol className="space-y-0">
        {items.map((i, idx) => (
          <li key={i.key} className="grid grid-cols-[5.5rem_1.5rem_minmax(0,1fr)] gap-x-2 sm:grid-cols-[7.5rem_1.5rem_minmax(0,1fr)]">
            <time dateTime={i.at} className="pt-0.5 text-right text-xs leading-5 tabular-nums text-muted">
              {new Date(i.at).toLocaleTimeString()}
              <span className="block">{new Date(i.at).toLocaleDateString()}</span>
            </time>
            <span className="relative flex justify-center" aria-hidden="true">
              {idx < items.length - 1 && <span className="absolute top-6 bottom-0 w-0.5 bg-line" />}
              <span className={`relative z-[1] mt-0.5 flex size-6 items-center justify-center rounded-full border bg-card ${i.who.startsWith("Rules") ? "border-primary text-primary" : "border-line text-ink"}`}>
                <Icon name={i.icon} size={14} />
              </span>
            </span>
            <div className="min-w-0 pb-3">
              <p className="text-sm leading-6">
                <strong>{i.title}</strong>
              </p>
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-muted">
                <span className="font-bold text-ink">{i.who}</span>
                {i.detail && <span>{i.detail}</span>}
                {!i.current && <StatusPill tone="neutral" icon="history">Earlier run</StatusPill>}
              </p>
            </div>
          </li>
        ))}
      </ol>
      {review.audit.length > 0 && (
        <details className="text-sm">
          <summary className="inline-flex min-h-10 cursor-pointer items-center py-1 font-bold text-primary">Full audit log ({review.audit.length} events)</summary>
          <div className="overflow-x-auto">
          <table className="mt-1 w-full min-w-[28rem] text-xs">
            <caption className="sr-only">Audit log for this case, oldest first as recorded by the server</caption>
            <thead>
              <tr className="text-left text-muted">
                <th scope="col" className="py-1 pr-2 font-bold">#</th>
                <th scope="col" className="py-1 pr-2 font-bold">Time</th>
                <th scope="col" className="py-1 pr-2 font-bold">Event</th>
                <th scope="col" className="py-1 pr-2 font-bold">By</th>
                <th scope="col" className="py-1 font-bold">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {review.audit.map((a) => (
                <tr key={a.seq} className="border-t border-subtle align-top">
                  <td className="py-1 pr-2 font-mono text-muted">{a.seq}</td>
                  <td className="py-1 pr-2 tabular-nums">
                    <time dateTime={a.timestamp}>{new Date(a.timestamp).toLocaleString()}</time>
                  </td>
                  <td className="py-1 pr-2 font-bold">{ACTION_WORDS[a.action] ?? a.action.replaceAll("_", " ")}</td>
                  <td className="py-1 pr-2">{roleLabel(a.actor_role)}</td>
                  <td className="py-1">
                    {a.outcome === "success" ? (
                      "Recorded"
                    ) : (
                      <span className="inline-flex items-center gap-1 font-bold text-warning">
                        <Icon name="alert" size={14} /> {a.outcome.replaceAll("_", " ")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </details>
      )}
    </SectionCard>
  );
}
