"use client";

// Supervisor governance (GET /audit/governance, docs/17). Aggregates only: no case ids, tokens, reviewer ids or
// free text. Every rate shows its numerator, denominator and definition from the server; "no data" is never shown as
// zero; a small sample is flagged. Reviewers are not ranked. Charts have a text table with the same numbers.
import Link from "next/link";
import { useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { EmptyState, ErrorState, LoadingState, StaleNotice } from "@/components/review/States";
import { WorkstationBar, shellAction } from "@/components/review/WorkstationBar";
import { Notice, inputClass } from "@/components/ui";
import { barShare, formatRate, formatSeconds, isStale, type Governance } from "@/lib/review";
import { useMe, useNow, usePolled } from "@/lib/useReview";

type Period = "all" | "24h" | "7d" | "30d";
const PERIODS: { key: Period; label: string; hours: number | null }[] = [
  { key: "all", label: "All time", hours: null },
  { key: "24h", label: "Last 24 hours", hours: 24 },
  { key: "7d", label: "Last 7 days", hours: 24 * 7 },
  { key: "30d", label: "Last 30 days", hours: 24 * 30 },
];

export default function GovernancePage() {
  const [period, setPeriod] = useState<Period>("all");
  const path = useMemo(() => {
    const hours = PERIODS.find((p) => p.key === period)?.hours;
    if (!hours) return "audit/governance";
    // Rounded to the minute so the 15-second refresh keeps the same window and URL.
    const since = new Date(Math.floor(Date.now() / 60_000) * 60_000 - hours * 3_600_000).toISOString();
    return `audit/governance?since=${encodeURIComponent(since)}`;
  }, [period]);
  const g = usePolled<Governance>(path);
  const { me, failed: meFailed } = useMe();
  const now = useNow(5000);
  const d = g.data;
  const stale = d ? isStale(d.generated_at, now, g.offsetMs) || g.error !== null : false;

  return (
    <div className="workstation -mx-4 -my-6 sm:-my-8">
      <WorkstationBar title="Governance" icon="chart" me={me} meFailed={meFailed} meta="Aggregates only, no patient or reviewer identifiers">
        <Link href="/dashboard" className={shellAction}>
          <Icon name="list" size={16} /> Reviewer queue (read only)
        </Link>
      </WorkstationBar>

      <div className="mx-auto max-w-6xl space-y-4 px-4 py-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label htmlFor="period" className="block text-sm font-bold">
              Measurement period
            </label>
            <select id="period" value={period} onChange={(e) => setPeriod(e.target.value as Period)} className={`${inputClass} w-auto`}>
              {PERIODS.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          {d && (
            <p className="text-sm text-muted">
              {d.period.since ? `From ${new Date(d.period.since).toLocaleString()}` : "All recorded cases"}
              {d.period.until ? ` to ${new Date(d.period.until).toLocaleString()}` : " to now"} · basis: {d.period.basis} · server time {new Date(d.generated_at).toLocaleTimeString()}
            </p>
          )}
        </div>

        {g.error && !d && <ErrorState title="Could not load governance data" message={g.error} status={g.errorStatus} onRetry={() => void g.reload()} />}
        {!d && !g.error && <LoadingState label="Loading governance data…" />}
        {d && stale && <StaleNotice error={g.error} onRetry={() => void g.reload()} updatedAt={d.generated_at} />}
        {d && <GovernanceView d={d} />}
      </div>
    </div>
  );
}

function GovernanceView({ d }: { d: Governance }) {
  if (d.sample_size === 0) {
    return (
      <EmptyState icon="chart" title="No triaged cases in this period">
        Rates are not shown because there is nothing to divide by. This is “no data”, not zero.
      </EmptyState>
    );
  }
  const o = d.overrides;
  // `episodes` / `definition` arrived with the hardening pass (docs/17 §7); older servers fall back to case counts.
  const e = d.red_escalation as Governance["red_escalation"] & { episodes?: number; definition?: string };
  const episodes = e.episodes ?? e.red_cases;
  return (
    <div className="space-y-4">
      {d.small_sample_warning && (
        <Notice tone="warning" title={`Small sample: ${d.sample_size} cases`}>
          <p>With fewer than 30 cases, a single case moves these percentages a lot. Read them as counts, not trends.</p>
        </Notice>
      )}
      <Notice tone="info">
        <p>
          {o.interpretation} These figures describe how the review process is used. They do not rank reviewers and do not measure patient outcomes. Synthetic
          prototype data.
        </p>
      </Notice>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric title="Cases in period" value={String(d.sample_size)} sub="Cases with a triage run in the period" />
        <Metric title="Review completed" value={formatRate(d.review_completion.rate, d.review_completion.signed_off, d.review_completion.denominator)} sub={d.review_completion.definition} />
        <Metric title="Override rate" value={formatRate(o.rate, o.cases_with_override, o.denominator)} sub={o.definition} />
        <Metric title="Median time to sign-off" value={d.turnaround_seconds.n > 0 && d.turnaround_seconds.n < 30 ? `${formatSeconds(d.turnaround_seconds.median)} (only ${d.turnaround_seconds.n} case${d.turnaround_seconds.n === 1 ? "" : "s"} — not reliable)` : formatSeconds(d.turnaround_seconds.median)} sub={`${d.turnaround_seconds.definition} · n = ${d.turnaround_seconds.n}`} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <section aria-labelledby="reasons-heading" className="rounded-lg border border-subtle bg-card p-4">
          <h2 id="reasons-heading" className="text-base font-bold leading-6">
            Override reasons
          </h2>
          <p className="text-sm text-muted">
            {o.events} override event{o.events === 1 ? "" : "s"} ({o.raised} raised urgency, {o.lowered} lowered). Reason codes are a prototype list pending
            clinical governance review.
          </p>
          {o.events === 0 ? (
            <p className="mt-2 text-sm">No overrides recorded in this period (0 of {o.denominator} cases).</p>
          ) : (
            <BarTable caption="Override events by reason code" rows={o.reasons.map((r) => ({ label: r.label, count: r.count }))} total={o.events} />
          )}
        </section>

        <section aria-labelledby="red-heading" className="rounded-lg border border-subtle bg-card p-4">
          <h2 id="red-heading" className="text-base font-bold leading-6">
            RED acknowledgment ({e.window_seconds / 60}-minute target)
          </h2>
          <p className="text-sm text-muted">
            {episodes} RED episode{episodes === 1 ? "" : "s"} across {e.red_cases} case{e.red_cases === 1 ? "" : "s"}. {e.definition ?? ""} Notification channel:{" "}
            {e.notification_channel ?? "none — this prototype sends no alert"}.
          </p>
          {episodes === 0 ? (
            <p className="mt-2 text-sm">No RED episodes in this period.</p>
          ) : (
            <BarTable
              caption="RED episodes by acknowledgment state"
              total={episodes}
              rows={[
                { label: "Acknowledged within target", count: e.acknowledged_within_window },
                { label: "Acknowledged after target", count: e.acknowledged_late },
                { label: "Overdue, not acknowledged", count: e.overdue_unacknowledged, alert: true },
                { label: "Pending, within target", count: e.pending },
              ]}
            />
          )}
        </section>

        <section aria-labelledby="data-heading" className="rounded-lg border border-subtle bg-card p-4">
          <h2 id="data-heading" className="text-base font-bold leading-6">
            Missing information
          </h2>
          <p className="mt-1 text-2xl font-bold tabular-nums">{formatRate(d.insufficient_data.rate, d.insufficient_data.count, d.insufficient_data.denominator)}</p>
          <p className="text-sm text-muted">Cases whose latest rules result was “needs information” (insufficient data) ÷ cases in the period.</p>
        </section>

        <section aria-labelledby="ai-heading" className="rounded-lg border border-subtle bg-card p-4">
          <h2 id="ai-heading" className="text-base font-bold leading-6">
            Human decisions on AI-extracted fields
          </h2>
          <p className="text-sm text-muted">{d.ai_field_reviews.definition}</p>
          {d.ai_field_reviews.total === 0 ? (
            <p className="mt-2 text-sm">No AI-extracted field has been reviewed yet (no data).</p>
          ) : (
            <>
              <p className="mt-1 text-2xl font-bold tabular-nums">
                {formatRate(d.ai_field_reviews.disagreement_rate, (d.ai_field_reviews.by_outcome.corrected ?? 0) + (d.ai_field_reviews.by_outcome.rejected ?? 0), d.ai_field_reviews.total)}
                <span className="ml-2 text-sm font-normal text-muted">corrected or rejected</span>
              </p>
              <BarTable
                caption="Reviewer decisions on AI-extracted fields"
                total={d.ai_field_reviews.total}
                rows={Object.entries(d.ai_field_reviews.by_outcome).map(([k, v]) => ({ label: k[0].toUpperCase() + k.slice(1), count: v }))}
              />
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function Metric({ title, value, sub }: { title: string; value: string; sub: string }) {
  return (
    <section className="rounded-lg border border-subtle bg-card p-4">
      <h2 className="text-sm font-bold uppercase tracking-wide text-muted">{title}</h2>
      <p className="mt-1 text-xl font-bold tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted">{sub}</p>
    </section>
  );
}

/** A table that is also the chart: each row has a bar drawn in SVG, plus the count and share as text. */
function BarTable({ caption, rows, total }: { caption: string; rows: { label: string; count: number; alert?: boolean }[]; total: number }) {
  const counts = rows.map((r) => r.count);
  return (
    <table className="mt-3 w-full text-sm">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col" className="pb-1 font-bold">
            Category
          </th>
          <th scope="col" className="w-2/5 pb-1 font-bold">
            <span className="sr-only">Bar</span>
          </th>
          <th scope="col" className="pb-1 text-right font-bold">
            Count (share)
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className="border-t border-subtle">
            <th scope="row" className="py-1.5 pr-2 text-left font-normal">
              {r.alert && r.count > 0 && <Icon name="alert" size={14} className="mr-1 text-error" />}
              {r.label}
            </th>
            <td className="py-1.5">
              <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="h-3 w-full" aria-hidden="true" focusable="false">
                <rect x="0" y="0" width="100" height="10" fill="var(--border-subtle)" />
                <rect x="0" y="0" width={barShare(r.count, counts) * 100} height="10" fill={r.alert ? "var(--esc-pending-border)" : "var(--chart-1)"} />
              </svg>
            </td>
            <td className="py-1.5 pl-2 text-right font-mono tabular-nums whitespace-nowrap">
              {r.count} <span className="text-muted">({total ? Math.round((r.count / total) * 100) : 0}%)</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
