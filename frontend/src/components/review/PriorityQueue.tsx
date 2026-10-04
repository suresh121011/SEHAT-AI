"use client";

// Priority queue in the server's order (app/review_queue.py): RED → YELLOW → GREEN from the rules engine, raised but
// never lowered by a reviewer override, then oldest triage first; a case with an unacknowledged earlier RED stays RED. This component never sorts. Filters only hide rows,
// keeping the server order. Each row is a link (list of links, not a grid) with aria-current on the open case.
import Link from "next/link";

import { Icon } from "@/components/Icon";
import { StatusPill, UrgencyBadge } from "@/components/review/Badges";
import { EmptyState } from "@/components/review/States";
import { SCENARIO_WORDS, escalationView, filterQueue, formatClock, formatWaiting, missingLabel, priorityExplanation, type Queue, type QueueFilter, type QueueItem } from "@/lib/review";

const FILTERS: { key: QueueFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "RED", label: "RED" },
  { key: "YELLOW", label: "YELLOW" },
  { key: "GREEN", label: "GREEN" },
];

type Props = {
  queue: Queue;
  filter: QueueFilter;
  onFilter: (f: QueueFilter) => void;
  includeSignedOff: boolean;
  onIncludeSignedOff: (v: boolean) => void;
  selectedCaseId: string | null;
  now: number;
  offsetMs: number;
};

export function PriorityQueue({ queue, filter, onFilter, includeSignedOff, onIncludeSignedOff, selectedCaseId, now, offsetMs }: Props) {
  const rows = filterQueue(queue.items, filter);
  const total = queue.items.length;
  return (
    <section aria-labelledby="queue-heading" className="flex min-h-0 flex-col">
      <div className="space-y-2 border-b border-subtle p-3">
        <div className="flex items-baseline justify-between gap-2">
          <h2 id="queue-heading" className="text-base font-bold leading-6">
            Priority queue <span className="text-sm font-normal text-muted">({total})</span>
          </h2>
        </div>
        <div role="group" aria-label="Show urgency" className="flex flex-wrap gap-1">
          {FILTERS.map((f) => {
            const count = f.key === "all" ? total : queue.counts[f.key as "RED" | "YELLOW" | "GREEN"];
            const active = filter === f.key;
            return (
              <button
                key={f.key}
                type="button"
                aria-pressed={active}
                onClick={() => onFilter(f.key)}
                className={`min-h-9 rounded border px-2.5 text-sm font-bold ${active ? "border-primary bg-primary text-white" : "border-subtle bg-card text-ink hover:border-primary"}`}
              >
                {f.label} <span className="font-normal tabular-nums">{count}</span>
              </button>
            );
          })}
        </div>
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={includeSignedOff} onChange={(e) => onIncludeSignedOff(e.target.checked)} className="size-4 accent-primary" />
          Include signed-off cases
        </label>
        <p className="text-xs text-muted">
          <Icon name="scale" size={14} className="mr-1 align-[-2px]" />
          Order: rules-engine urgency (RED first), then longest waiting. A reviewer can move a case up, never down. AI output never changes the order.
        </p>
      </div>

      {rows.length === 0 ? (
        <div className="p-3">
          <EmptyState icon={total === 0 ? "check" : "list"} title={total === 0 ? "No cases are waiting for review" : "No case matches this filter"}>
            {total === 0 ? "New cases appear here after a health worker submits triage." : "Choose All to see every case."}
          </EmptyState>
        </div>
      ) : (
        <ol className="min-h-0 flex-1 divide-y divide-subtle overflow-y-auto">
          {rows.map((item) => (
            <QueueRow key={item.case_id} item={item} selected={item.case_id === selectedCaseId} now={now} offsetMs={offsetMs} />
          ))}
        </ol>
      )}
    </section>
  );
}

// Row scan order (left to right, top to bottom): urgency word → patient token → RED target state → what needs
// attention → review status; waiting time and scenario are de-emphasised (operational, not clinical priority).
function QueueRow({ item, selected, now, offsetMs }: { item: QueueItem; selected: boolean; now: number; offsetMs: number }) {
  const esc = item.escalation && item.escalation.state !== "acknowledged" ? escalationView(item.escalation, now, offsetMs) : null;
  const why = priorityExplanation(item);
  const missing = missingLabel(item);
  const signedOff = item.review_status === "signed_off";
  return (
    <li>
      <Link
        href={`/dashboard?case=${item.case_id}`}
        aria-current={selected ? "page" : undefined}
        className={`block border-l-4 px-3 py-2.5 hover:bg-primary-tint/60 ${selected ? "border-row-bar bg-row-selected" : item.priority_urgency === "RED" ? "border-urg-red" : "border-transparent"}`}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <UrgencyBadge urgency={item.priority_urgency} size="sm" />
          {/* Tokens never break mid-token (a split token is easy to misread); the RED-target pill wraps instead. */}
          <span className="whitespace-nowrap text-base font-bold leading-6">
            {item.patient_token}
          </span>
          {esc && (
            <span className="ml-auto">
              <StatusPill tone="danger" icon={esc.display === "pending" ? "clock" : "alert"}>
                <span className="tabular-nums">{esc.display === "pending" ? `Ack due ${formatClock(esc.remainingMs)}` : esc.display === "overdue" ? "Ack overdue" : "Past ack target"}</span>
              </StatusPill>
            </span>
          )}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {missing && <StatusPill tone="attention" icon="question">{missing}</StatusPill>}
            {item.determination === "outside_validated_population" && <StatusPill tone="attention" icon="info">Outside rule population</StatusPill>}
            {signedOff ? <StatusPill tone="done" icon="check">Signed off (reviewed)</StatusPill> : <StatusPill tone="neutral" icon="eye">Awaiting review</StatusPill>}
        </div>
        {why && <p className="mt-1 text-xs leading-5 text-ink">{why}</p>}
        <p className="mt-1 flex items-center gap-1 text-xs leading-5 text-muted">
          <Icon name="clock" size={14} />
          <span className="tabular-nums">Waiting {formatWaiting(item.waiting_seconds)}</span>
          <span className="sr-only">(operational waiting time, not clinical priority)</span>
          <span aria-hidden="true">·</span>
          <span>{SCENARIO_WORDS[item.scenario] ?? item.scenario}</span>
        </p>
      </Link>
    </li>
  );
}
