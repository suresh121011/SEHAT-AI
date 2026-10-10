"use client";

// Read-only overview for the medical officer and supervisor (redesign). Every number comes from GET /triage/queue
// (open cases only: awaiting review, or signed off with an open RED acknowledgement). Nothing here sorts, scores or
// changes urgency: the list is the server's order, cut to the first rows. Actions happen on the review workstation.
import Link from "next/link";

import { Icon } from "@/components/Icon";
import { MetricCard } from "@/components/MetricCard";
import { StatusPill, UrgencyBadge } from "@/components/review/Badges";
import { EmptyState, ErrorState, QueueSkeleton, StaleNotice } from "@/components/review/States";
import { ButtonLink, PageHeader } from "@/components/ui";
import { SCENARIO_WORDS, escalationView, formatClock, formatWaiting, isStale, missingLabel, openEscalations, type Queue, type QueueItem, type Urgency } from "@/lib/review";
import { useMe, useNow, useQueue } from "@/lib/useReview";

const TOP_ROWS = 6;

function greeting(hour: number) {
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

export default function OverviewPage() {
  const queue = useQueue(false, false);
  const now = useNow();
  const { me } = useMe();
  const q = queue.data;
  const stale = q ? isStale(q.generated_at, now, queue.offsetMs) || queue.error !== null : false;
  const isSupervisor = me?.role === "supervisor";

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={greeting(new Date(now).getHours())}
        title="Overview"
        description={
          <p>
            Open cases in the facilities this account can see.{" "}
            {q && <span className="tabular-nums">Server update {new Date(q.generated_at).toLocaleTimeString()}.</span>}
          </p>
        }
        actions={
          <>
            <button type="button" onClick={() => void queue.reload()} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-subtle bg-card px-4 text-sm font-bold text-ink hover:border-strong">
              <Icon name="refresh" size={16} /> Refresh
            </button>
            <ButtonLink href="/dashboard">
              <Icon name={isSupervisor ? "list" : "stethoscope"} size={18} /> {isSupervisor ? "Open review queue" : "Open review workstation"}
            </ButtonLink>
          </>
        }
      />

      {queue.error && !q && <ErrorState title="Could not load the queue" message={queue.error} status={queue.errorStatus} onRetry={() => void queue.reload()} />}
      {!q && !queue.error && <QueueSkeleton label="Loading the overview…" />}
      {q && stale && <StaleNotice error={queue.error} onRetry={() => void queue.reload()} updatedAt={q.generated_at} />}
      {q && <OverviewBody q={q} now={now} offsetMs={queue.offsetMs} />}
    </div>
  );
}

function OverviewBody({ q, now, offsetMs }: { q: Queue; now: number; offsetMs: number }) {
  const awaiting = q.items.filter((i) => i.review_status === "awaiting_review").length;
  const escalations = openEscalations(q.items);
  const overdue = escalations.filter((i) => i.escalation && escalationView(i.escalation, now, offsetMs).display !== "pending").length;
  const needsInfo = q.items.filter((i) => i.determination === "insufficient_data").length;
  const windowMin = Math.round(q.escalation_window_seconds / 60);

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <MetricCard label="Awaiting review" icon="eye" value={awaiting} sub={`${q.items.length} open case${q.items.length === 1 ? "" : "s"} in the queue`} href="/dashboard" linkLabel="Review" />
        <MetricCard
          label="awaiting ack"
          urgency="RED"
          value={escalations.length}
          sub={overdue > 0 ? `${overdue} past the ${windowMin}-minute target` : `${windowMin}-minute review target`}
        />
        <MetricCard label="Needs information" icon="question" attention={needsInfo > 0} value={needsInfo} sub="Rules result: insufficient data" />
        <MetricCard label="Median wait" icon="clock" value={medianWait(q.items)} sub="Operational, not clinical priority" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <section aria-labelledby="pq-heading" className="min-w-0 rounded-xl border border-subtle bg-card shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
            <div>
              <h2 id="pq-heading" className="text-base font-bold">
                Priority queue
              </h2>
              <p className="text-sm text-muted">Server order: rules-engine urgency (RED first), then longest waiting.</p>
            </div>
            <Link href="/dashboard" className="inline-flex min-h-10 items-center gap-1 text-sm font-bold text-primary underline-offset-4 hover:underline">
              View all <Icon name="arrowRight" size={14} />
            </Link>
          </div>
          {q.items.length === 0 ? (
            <div className="p-4">
              <EmptyState icon="check" title="No cases are waiting for review">
                New cases appear here after a health worker submits triage.
              </EmptyState>
            </div>
          ) : (
            <ol className="divide-y divide-subtle">
              {q.items.slice(0, TOP_ROWS).map((item) => (
                <OverviewRow key={item.case_id} item={item} now={now} offsetMs={offsetMs} />
              ))}
            </ol>
          )}
          {q.items.length > TOP_ROWS && <p className="border-t border-subtle px-4 py-2.5 text-sm text-muted">{q.items.length - TOP_ROWS} more in the review workstation.</p>}
        </section>

        <div className="space-y-4">
          <UrgencyMix counts={q.counts} />
          <section aria-labelledby="ack-heading" className="rounded-xl border border-subtle bg-card p-4 shadow-card">
            <h2 id="ack-heading" className="flex items-center gap-2 text-base font-bold">
              <Icon name="clock" size={18} /> RED acknowledgements
            </h2>
            <p className="mt-1 text-sm text-muted">Display only: this prototype sends no alert or notification.</p>
            {escalations.length === 0 ? (
              <p className="mt-3 text-sm">No RED case is waiting for acknowledgement.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {escalations.slice(0, 5).map((i) => {
                  const v = escalationView(i.escalation!, now, offsetMs);
                  return (
                    <li key={i.case_id}>
                      <Link href={`/dashboard?case=${i.case_id}`} className="flex min-h-11 items-center justify-between gap-2 rounded-lg border border-esc-pending-line/30 bg-esc-pending px-3 py-2 hover:border-esc-pending-line">
                        <span className="font-bold text-esc-pending-ink">{i.patient_token}</span>
                        <span className="text-sm font-bold tabular-nums text-esc-pending-ink">{v.display === "pending" ? `Due in ${formatClock(v.remainingMs)}` : "Overdue"}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

function OverviewRow({ item, now, offsetMs }: { item: QueueItem; now: number; offsetMs: number }) {
  const esc = item.escalation && item.escalation.state !== "acknowledged" ? escalationView(item.escalation, now, offsetMs) : null;
  const missing = missingLabel(item);
  return (
    <li>
      <Link href={`/dashboard?case=${item.case_id}`} className="grid gap-x-4 gap-y-1.5 px-4 py-3 hover:bg-surface-2 sm:grid-cols-[6.5rem_minmax(0,1fr)_auto] sm:items-center">
        <span>
          <UrgencyBadge urgency={item.priority_urgency} size="sm" />
        </span>
        <span className="min-w-0">
          <span className="block font-bold leading-6">{item.patient_token}</span>
          <span className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
            {SCENARIO_WORDS[item.scenario] ?? item.scenario} · {item.facility_code}
            {missing && <StatusPill tone="attention" icon="question">{missing}</StatusPill>}
            {esc && <StatusPill tone="danger" icon="clock">{esc.display === "pending" ? `Ack due ${formatClock(esc.remainingMs)}` : "Ack overdue"}</StatusPill>}
          </span>
        </span>
        <span className="flex items-center gap-1 text-sm tabular-nums text-muted sm:justify-end">
          <Icon name="clock" size={14} /> Waiting {formatWaiting(item.waiting_seconds)}
        </span>
      </Link>
    </li>
  );
}

/** Stacked bar of the server's open-queue counts, with every number also written out (never colour alone). */
function UrgencyMix({ counts }: { counts: Record<Urgency, number> }) {
  const total = counts.RED + counts.YELLOW + counts.GREEN;
  const rows: { u: Urgency; fill: string; meaning: string }[] = [
    { u: "RED", fill: "var(--urg-red-bg)", meaning: "Immediate attention" },
    { u: "YELLOW", fill: "var(--urg-yellow-bg)", meaning: "Urgent review" },
    { u: "GREEN", fill: "var(--urg-green-fg)", meaning: "Routine queue (not “safe”)" },
  ];
  return (
    <section aria-labelledby="mix-heading" className="rounded-xl border border-subtle bg-card p-4 shadow-card">
      <h2 id="mix-heading" className="text-base font-bold">
        Open cases by urgency
      </h2>
      <p className="text-sm text-muted">Queue priority from the rules engine (raised, never lowered, by a reviewer).</p>
      {total > 0 && (
        <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
          {rows.map((r) => (counts[r.u] > 0 ? <span key={r.u} style={{ width: `${(counts[r.u] / total) * 100}%`, background: r.fill }} className="border-r-2 border-card last:border-r-0" /> : null))}
        </div>
      )}
      <dl className="mt-3 space-y-2">
        {rows.map((r) => (
          <div key={r.u} className="flex items-center justify-between gap-2">
            <dt className="flex items-center gap-2 text-sm">
              <UrgencyBadge urgency={r.u} size="sm" />
              <span className="text-muted">{r.meaning}</span>
            </dt>
            <dd className="font-bold tabular-nums">{counts[r.u]}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function medianWait(items: QueueItem[]): string {
  const s = items.map((i) => i.waiting_seconds).filter((n) => Number.isFinite(n) && n >= 0).sort((a, b) => a - b);
  if (s.length === 0) return "—";
  const mid = Math.floor(s.length / 2);
  return formatWaiting(s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2);
}
