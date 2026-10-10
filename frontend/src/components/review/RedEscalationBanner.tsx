"use client";

// Persistent RED review-target banner. The deadline, state and acknowledgment come from the server
// (app/review_queue.py); the countdown here is display only, corrected by the server clock offset. No notification
// exists in this prototype, and the banner says so. Screen readers hear state changes only (new RED, past target,
// overdue), never the per-second countdown. Acknowledging happens inside the case, where the case is visible.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { escalationView, formatClock, type QueueItem } from "@/lib/review";

type Props = { items: QueueItem[]; now: number; offsetMs: number; windowSeconds: number; selectedCaseId: string | null; stale: boolean };

export function RedEscalationBanner({ items, now, offsetMs, windowSeconds, selectedCaseId, stale }: Props) {
  const [announcement, setAnnouncement] = useState("");
  const seen = useRef<Map<string, string>>(new Map());

  const views = items.map((i) => ({ item: i, view: escalationView(i.escalation!, now, offsetMs) }));
  const key = views.map((v) => `${v.item.triage_run_id}:${v.view.display}`).join("|");

  useEffect(() => {
    const msgs: string[] = [];
    const next = new Map<string, string>();
    for (const { item, view } of views) {
      next.set(item.triage_run_id, view.display);
      const before = seen.current.get(item.triage_run_id);
      if (before === view.display) continue;
      if (view.display === "overdue") msgs.push(`RED case ${item.patient_token} is overdue for acknowledgment.`);
      else if (view.display === "past_target") msgs.push(`RED case ${item.patient_token} has passed the ${windowSeconds / 60}-minute target.`);
      else if (before === undefined) msgs.push(`New RED case ${item.patient_token} awaiting acknowledgment.`);
    }
    seen.current = next;
    if (msgs.length) setAnnouncement(msgs.join(" "));
    // `key` captures every state change that matters; the countdown itself is deliberately excluded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, windowSeconds]);

  // One persistent live region outside the branches: a region mounted already holding text is often not read.
  const live = (
    <p className="sr-only" role="status" aria-live="polite">
      {announcement}
    </p>
  );

  if (items.length === 0) {
    return (
      <>
      {live}
      <section aria-label="RED review target" className="flex items-center gap-2 rounded-lg border border-esc-ack-line/40 bg-esc-ack px-4 py-2 text-sm text-esc-ack-ink">
        <Icon name="check" size={16} />
        No RED case is unacknowledged{stale ? " as of the last successful refresh" : " right now"}. This is a queue status, not a statement about any patient.
      </section>
      </>
    );
  }

  const overdue = views.some((v) => v.view.display !== "pending");
  return (
    <>
    {live}
    <section aria-labelledby="esc-heading" className={`overflow-hidden rounded-xl border-2 ${overdue ? "border-esc-overdue" : "border-esc-pending-line"} bg-esc-pending`}>
      <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 ${overdue ? "bg-esc-overdue text-esc-overdue-ink" : "text-esc-pending-ink"}`}>
        <Icon name={overdue ? "alert" : "clock"} size={20} />
        <h2 id="esc-heading" className="text-lg font-bold leading-7">
          {items.length} RED {items.length === 1 ? "case" : "cases"} awaiting acknowledgment
        </h2>
        <p className="text-sm">
          Target: acknowledge within {windowSeconds / 60} minutes of the RED triage (server time).
          <strong> No SMS, call or alert is sent by this prototype — this screen is the only indicator.</strong> If you leave this screen, no one is notified — escalate by phone or in person using your facility’s usual process.
        </p>
      </div>
      <ul className="divide-y divide-esc-pending-line/30">
        {views.map(({ item, view }) => (
          <li key={item.triage_run_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-esc-pending-ink">
            <span className="inline-flex items-center gap-1.5 font-bold">
              <Icon name="octagon" size={16} />
              {item.patient_token}
            </span>
            {view.display === "pending" && (
              <span className="inline-flex items-center gap-1 font-mono text-base tabular-nums">
                <Icon name="clock" size={16} />
                <span>
                  <span className="sr-only">Time left to acknowledge: </span>
                  {formatClock(view.remainingMs)} left
                </span>
              </span>
            )}
            {view.display === "past_target" && (
              <span className="inline-flex flex-wrap items-center gap-x-1 rounded-md bg-esc-overdue px-2 py-0.5 font-bold text-esc-overdue-ink">
                <Icon name="alert" size={16} />
                Past target by <span className="font-mono">{formatClock(view.remainingMs)}</span>
                <span className="font-normal">· server confirmation on next refresh</span>
              </span>
            )}
            {view.display === "overdue" && (
              <span className="inline-flex flex-wrap items-center gap-x-1 rounded-md bg-esc-overdue px-2 py-0.5 font-bold text-esc-overdue-ink">
                <Icon name="alert" size={16} />
                OVERDUE by <span className="font-mono">{formatClock(view.remainingMs)}</span>
                <span className="font-normal">· recorded in the audit log</span>
              </span>
            )}
            {item.escalation?.anchor_source && item.escalation.anchor_source !== "triage_run" && (
              <span className="text-xs">
                Clock started {item.escalation.anchor_source === "reviewer_override" ? "when a reviewer raised the case to RED" : "at an earlier RED run of this case"}
                {item.rules_urgency !== "RED" && ` — latest run is ${item.rules_urgency}, but that RED was never acknowledged`}
              </span>
            )}
            {item.case_id === selectedCaseId ? (
              <span className="ml-auto text-sm font-bold">Open below</span>
            ) : (
              <Link href={`/dashboard?case=${item.case_id}`} className="ml-auto inline-flex min-h-10 items-center gap-1 rounded px-2 font-bold underline underline-offset-4">
                Open case <span className="sr-only">{item.patient_token}</span>
                <Icon name="arrowRight" size={16} />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </section>
    </>
  );
}
