"use client";

// "Check these first" (P0-2; backend app/verification_queue.py). An order for checking what is still machine-read: it
// confirms nothing and never changes urgency. A document row opens in the review panel beside it; a spoken value is
// read back on the voice step.

import Link from "next/link";

import { Card } from "@/components/ui";
import { PRIORITY_WORDS, type Priority, type Queue, type QueueItem, reasonWords, urgencyWords } from "@/lib/verificationQueue";

const ORDER: Priority[] = ["match_document", "could_change_urgency", "abnormal_lab", "routine"];
const TONE: Record<string, string> = { error: "text-error", warning: "text-warning", info: "text-ink" };

export function CheckFirstPanel({ caseId, queue, selectedFieldId, onOpen, voiceLinks = true }: {
  caseId: string;
  queue: Queue | null;
  selectedFieldId: string | null;
  onOpen: (item: QueueItem) => void;
  /** False where the voice step is not reachable (the medical officer's dashboard): spoken values are listed, not linked. */
  voiceLinks?: boolean;
}) {
  if (!queue) return null;
  if (queue.items.length === 0) {
    return (
      <Card as="aside" className="text-sm">
        <p role="status"><strong>Nothing left to check.</strong> Every machine-read value on this case has a decision.</p>
      </Card>
    );
  }
  return (
    <Card as="aside" className="space-y-3 text-sm" >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-bold">What to check, in order</h2>
        <span aria-live="polite" className="text-xs text-muted">{queue.counts.total} still to check</span>
      </div>
      <p className="text-xs text-muted">{queue.note}</p>
      {ORDER.filter((p) => queue.items.some((i) => i.priority === p)).map((p) => (
        <section key={p} aria-label={PRIORITY_WORDS[p].title} className="space-y-1">
          <h3 className={`font-bold ${TONE[PRIORITY_WORDS[p].tone]}`}>{PRIORITY_WORDS[p].title}</h3>
          <ul className="space-y-1">
            {queue.items.filter((i) => i.priority === p).map((i) => {
              const hypo = urgencyWords(i.urgency_if_confirmed);
              const body = (
                <>
                  <span className="flex flex-wrap items-baseline justify-between gap-2">
                    <strong>{i.label}</strong>
                    {i.shown && <span className="font-mono text-xs">{i.shown} <span className="text-muted">(machine-read)</span></span>}
                  </span>
                  {i.reasons.length > 0 && <span className="block text-xs">{reasonWords(i.reasons).join(" · ")}</span>}
                  {hypo && <span className="block text-xs font-bold text-error">{hypo}</span>}
                </>
              );
              const cls = `block w-full rounded-lg border p-2 text-left ${i.ref.field_id && i.ref.field_id === selectedFieldId ? "border-primary ring-2 ring-primary" : "border-subtle hover:bg-surface-1"}`;
              return (
                <li key={i.item_id}>
                  {i.act_on === "voice" && voiceLinks ? (
                    <Link href={`/intake/voice?case=${caseId}`} className={cls}>
                      {body}
                      <span className="block text-xs text-primary underline">Read it back on the voice step</span>
                    </Link>
                  ) : i.act_on === "voice" ? (
                    <div className={cls}>
                      {body}
                      <span className="block text-xs text-muted">Read back to the patient by the health worker on the voice step</span>
                    </div>
                  ) : (
                    <button type="button" onClick={() => onOpen(i)} className={cls} aria-pressed={!!i.ref.field_id && i.ref.field_id === selectedFieldId}>
                      {body}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </Card>
  );
}
