"use client";

// Phase 8 reviewer workstation (docs/17): priority queue | case workspace | evidence & explanation, under a
// persistent RED review-target banner. One page with ?case= so the queue and the banner stay in view while a case is
// open. Data polls every 15 s (paused while a dialog is open or the tab is hidden); the last good data is kept and
// marked stale on error, so a RED case never disappears because one refresh failed.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { CaseWorkspace } from "@/components/review/CaseWorkspace";
import { CounterfactualPanel } from "@/components/review/CounterfactualPanel";
import { EvidencePanel } from "@/components/review/EvidencePanel";
import { PriorityQueue } from "@/components/review/PriorityQueue";
import { SectionCard } from "@/components/review/Panel";
import { RedEscalationBanner } from "@/components/review/RedEscalationBanner";
import type { ActionDone } from "@/components/review/ReviewActions";
import { EmptyState, ErrorState, LoadingState, QueueSkeleton, StaleNotice } from "@/components/review/States";
import { WorkstationBar, shellAction } from "@/components/review/WorkstationBar";
import { Notice } from "@/components/ui";
import { isStale, openEscalations, serverOrderLooksValid, type QueueFilter } from "@/lib/review";
import { useCaseReview, useMe, useNow, useQueue } from "@/lib/useReview";

export default function ReviewerDashboardPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading the reviewer queue…" />}>
      <ReviewerDashboard />
    </Suspense>
  );
}

function ReviewerDashboard() {
  const params = useSearchParams();
  const router = useRouter();
  const caseId = params.get("case");
  const [filter, setFilter] = useState<QueueFilter>("all");
  const [includeSignedOff, setIncludeSignedOff] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [done, setDone] = useState<ActionDone | null>(null);
  const doneRef = useRef<HTMLDivElement>(null);
  const now = useNow();
  const queue = useQueue(includeSignedOff, dialogOpen);
  const review = useCaseReview(caseId, dialogOpen);

  const { me, failed: meFailed } = useMe();
  const role = me?.role ?? null;

  useEffect(() => setDone(null), [caseId]);
  // After an action the button that opened the dialog may be disabled (e.g. "Signed off"); move focus to the result.
  useEffect(() => {
    if (done) doneRef.current?.focus();
  }, [done]);

  useEffect(() => {
    if (queue.data && !serverOrderLooksValid(queue.data.items)) console.warn("Reviewer queue: server order differs from the documented rule; showing server order unchanged.");
  }, [queue.data]);

  const reloadBoth = useCallback(async () => {
    await Promise.all([queue.reload(), review.reload()]);
  }, [queue, review]);

  const onAction = useCallback(
    (d: ActionDone) => {
      setDone(d);
      void reloadBoth();
    },
    [reloadBoth],
  );

  // Medical images still needing "Findings reviewed" (reported by the evidence panel); sign-off waits for them.
  const [imagePending, setImagePending] = useState<{ caseId: string; count: number } | null>(null);
  const onImagesPending = useCallback((count: number) => setImagePending(caseId ? { caseId, count } : null), [caseId]);
  const imageAcksPending = imagePending && imagePending.caseId === caseId ? imagePending.count : 0;

  const q = queue.data;
  const stale = q ? isStale(q.generated_at, now, queue.offsetMs) || queue.error !== null : false;
  const escalations = q ? openEscalations(q.items) : [];
  const r = review.data;

  return (
    <div className="workstation -mx-4 -my-6 sm:-my-8">
      {/* Workstation bar on the dark shell, with the persistent facility-access line from the server. */}
      <WorkstationBar
        title="Reviewer workstation"
        icon="list"
        me={me}
        meFailed={meFailed}
        meta={q ? `Updated ${new Date(q.generated_at).toLocaleTimeString()} (server)` : "Not loaded yet"}
      >
        <button type="button" onClick={() => void reloadBoth()} className={shellAction}>
          <Icon name="refresh" size={16} /> Refresh
        </button>
        {role === "supervisor" && (
          <Link href="/dashboard/governance" className={shellAction}>
            <Icon name="chart" size={16} /> Governance
          </Link>
        )}
      </WorkstationBar>

      <div className="space-y-3 px-4 py-3">
        <p className="text-xs text-muted">
          Research prototype, not a clinically validated device. Urgency comes from fixed rules; AI may only assist and never orders this queue. A medical officer reviews
          every case. Synthetic data only.
        </p>

        {queue.error && !q && <ErrorState title="Could not load the queue" message={queue.error} status={queue.errorStatus} onRetry={() => void queue.reload()} />}
        {!q && !queue.error && <QueueSkeleton label="Loading the reviewer queue…" />}
        {q && stale && <StaleNotice error={queue.error} onRetry={() => void reloadBoth()} updatedAt={q.generated_at} />}
        {q && <RedEscalationBanner items={escalations} now={now} offsetMs={queue.offsetMs} windowSeconds={q.escalation_window_seconds} selectedCaseId={caseId} stale={stale} />}
      </div>

      {q && (
        <div className="grid gap-3 px-4 pb-6 md:grid-cols-[17rem_minmax(0,1fr)] xl:grid-cols-[19rem_minmax(0,1fr)_22rem]">
          <section aria-label="Priority queue" className={`${caseId ? "hidden md:flex" : "flex"} max-h-[calc(100vh-9rem)] min-h-[20rem] flex-col overflow-hidden rounded-lg border border-subtle bg-card md:sticky md:top-2`}>
            <PriorityQueue
              queue={q}
              filter={filter}
              onFilter={setFilter}
              includeSignedOff={includeSignedOff}
              onIncludeSignedOff={setIncludeSignedOff}
              selectedCaseId={caseId}
              now={now}
              offsetMs={queue.offsetMs}
            />
          </section>

          <section aria-label="Case review" className={`${caseId ? "" : "hidden md:block"} min-w-0 rounded-lg border border-subtle bg-page`}>
            {caseId && (
              <div className="border-b border-subtle px-4 py-2 md:hidden">
                <button type="button" onClick={() => router.push("/dashboard")} className="inline-flex min-h-10 items-center gap-1 font-bold text-primary underline underline-offset-4">
                  <Icon name="arrowLeft" size={16} /> Back to queue
                </button>
              </div>
            )}
            {done && (
              <div ref={doneRef} tabIndex={-1} className="px-4 pt-4 outline-none">
                <Notice tone="info" role="status" title="Recorded by the server">
                  <p>{done.message}</p>
                </Notice>
              </div>
            )}
            {!caseId && (
              <div className="p-6">
                <EmptyState icon="arrowLeft" title="Select a case from the queue">Cases are listed RED first, then by longest waiting. Open one to review the rules result, the evidence and the actions.</EmptyState>
              </div>
            )}
            {caseId && !r && review.error && (
              <div className="p-4">
                {review.errorStatus === 404 || review.errorStatus === 400 ? (
                  // Not found and out-of-facility are deliberately the same answer from the server (no existence leak).
                  <EmptyState title={review.errorStatus === 404 ? "Case not found" : "This case link is not valid"}>
                    {review.errorStatus === 404
                      ? "It does not exist, or it is not in the facilities this account can see. Choose a case from the queue."
                      : "Check the link, or choose a case from the queue."}
                  </EmptyState>
                ) : (
                  <ErrorState title="Could not load this case" message={review.error} status={review.errorStatus} onRetry={() => void review.reload()} />
                )}
              </div>
            )}
            {caseId && !r && !review.error && <LoadingState label="Loading case…" />}
            {caseId && r && (
              <>
                {review.error && (
                  <div className="px-4 pt-4">
                    <StaleNotice error={review.error} onRetry={() => void review.reload()} updatedAt={r.generated_at} />
                  </div>
                )}
                <CaseWorkspace review={r} now={now} offsetMs={review.offsetMs} onAction={onAction} onStale={() => void reloadBoth()} onDialogChange={setDialogOpen} imageAcksPending={imageAcksPending} />
              </>
            )}
          </section>

          {caseId && r?.latest && (
            <section aria-label="Evidence and explanation" className="min-w-0 space-y-3 md:col-start-2 xl:col-start-auto">
              <CounterfactualPanel caseId={r.case.case_id} runId={r.latest.triage_run_id} actual={r.latest.rules_urgency} canCompute={r.can_review && r.clinical_content_available && r.latest.has_input} />
              <SectionCard id="evidence-heading" title="Source evidence" icon="document">
                {r.can_review ? (
                  <EvidencePanel caseId={r.case.case_id} consent={r.consent} clinical={r.clinical_content_available} onImagesPending={onImagesPending} />
                ) : (
                  <p className="text-sm text-muted">Source evidence is shown to the medical officer reviewing the case.</p>
                )}
              </SectionCard>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
