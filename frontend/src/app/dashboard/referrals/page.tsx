"use client";

// Referral tracking (docs/21; docs/05 §5.2): open referrals from the facilities this account can see, overdue first.
// Overdue = no update for 48 hours before the patient reached the referral facility. SEHAT sends no alert: this list is
// where an overdue referral shows. Metadata only, the same for supervisors; the letter is in the case workspace.
import Link from "next/link";
import { useState } from "react";

import { Icon } from "@/components/Icon";
import { MetricCard } from "@/components/MetricCard";
import { StatusPill, UrgencyBadge } from "@/components/review/Badges";
import { EmptyState, ErrorState, QueueSkeleton } from "@/components/review/States";
import { PageHeader } from "@/components/ui";
import { sinceText, type Tracking } from "@/lib/referral";
import { useMe, usePolled } from "@/lib/useReview";

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export default function ReferralsPage() {
  const tracking = usePolled<Tracking>("referral/active");
  const { me } = useMe();
  const [overdueOnly, setOverdueOnly] = useState(false);
  const t = tracking.data;
  const items = t ? (overdueOnly ? t.items.filter((i) => i.overdue) : t.items) : [];
  const canOpenCase = me?.role === "medical_officer";

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Closure tracking"
        title="Referrals"
        description={
          <p>
            Open referrals from the facilities this account can see. A referral is overdue when nobody has recorded an update for{" "}
            {t?.overdue_threshold_hours ?? 48} hours before the patient reached the referral facility. SEHAT sends no alerts: check this list.
          </p>
        }
        actions={
          <button
            type="button"
            onClick={() => void tracking.reload()}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-subtle bg-card px-4 text-sm font-bold text-ink hover:border-strong"
          >
            <Icon name="refresh" size={16} /> Refresh
          </button>
        }
      />

      {t && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <MetricCard label="Overdue" value={t.counts.overdue} icon="clock" attention={t.counts.overdue > 0} sub={`No update for ${t.overdue_threshold_hours} h`} />
          <MetricCard label="Referred" value={t.counts.referred} icon="send" />
          <MetricCard label="On the way" value={t.counts.in_transit} icon="arrowRight" />
          <MetricCard label="Reached" value={t.counts.reached} icon="building" />
          <MetricCard label="Seen" value={t.counts.seen} icon="check" />
        </div>
      )}

      <div role="group" aria-label="Which referrals to show" className="flex gap-2">
        {[
          { key: false, label: "All open" },
          { key: true, label: "Overdue only" },
        ].map((f) => (
          <button
            key={f.label}
            type="button"
            aria-pressed={overdueOnly === f.key}
            onClick={() => setOverdueOnly(f.key)}
            className={`min-h-11 rounded-lg border px-4 text-sm font-bold ${overdueOnly === f.key ? "border-strong bg-surface-1" : "border-subtle bg-card"}`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {!t && !tracking.error && <QueueSkeleton rows={3} label="Loading referrals" />}
      {!t && tracking.error && (
        <ErrorState title="Referrals could not be loaded" message={tracking.error} onRetry={() => void tracking.reload()} status={tracking.errorStatus} />
      )}
      {t && items.length === 0 && (
        <EmptyState title={overdueOnly ? "No overdue referrals" : "No open referrals"} icon="check">
          <p>Referrals appear here when a medical officer refers a signed-off case, and leave when an outcome or a closure reason is recorded.</p>
        </EmptyState>
      )}
      {items.length > 0 && (
        <ul className="space-y-2" aria-label="Open referrals">
          {items.map((i) => (
            <li key={i.referral_id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-subtle bg-card p-3">
              <div className="space-y-0.5">
                <p className="font-bold">
                  {i.patient_token} <span className="font-normal text-muted">to {i.to_facility.name}</span>
                </p>
                <p className="text-sm text-muted">
                  From {i.from_facility.name} · referred {when(i.created_at)}
                </p>
                <p className={`text-sm ${i.overdue ? "font-bold text-ink" : "text-muted"}`}>{sinceText(i.hours_since_update, i.overdue, i.overdue_threshold_hours)}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <UrgencyBadge urgency={i.urgency} size="sm" />
                <StatusPill tone={i.overdue ? "attention" : "neutral"} icon={i.overdue ? "clock" : "send"}>
                  {i.overdue ? "Overdue" : i.status_label}
                </StatusPill>
                {canOpenCase && (
                  <Link href={`/dashboard?case=${encodeURIComponent(i.case_id)}`} className="inline-flex min-h-11 items-center rounded-lg border border-subtle px-3 text-sm font-bold hover:border-strong">
                    Open case
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
