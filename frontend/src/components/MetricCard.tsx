// Compact metric tile. Urgency tiles carry the urgency word and its icon shape as well as its colour (never colour
// alone); every other tile is neutral teal. `value` is whatever the server returned, formatted by the caller.
import Link from "next/link";
import type { ReactNode } from "react";

import { Icon, type IconName } from "@/components/Icon";
import type { Urgency } from "@/lib/review";

const URG: Record<Urgency, { icon: IconName; chip: string; word: string }> = {
  RED: { icon: "octagon", chip: "bg-urg-red text-urg-red-ink", word: "RED" },
  YELLOW: { icon: "alert", chip: "bg-urg-yellow text-urg-yellow-ink", word: "YELLOW" },
  GREEN: { icon: "circle", chip: "bg-urg-green text-urg-green-ink border border-urg-green-ink/30", word: "GREEN" },
};

export function MetricCard({ label, value, sub, icon = "info", urgency, attention = false, href, linkLabel }: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  icon?: IconName;
  urgency?: Urgency;
  /** Amber "needs attention" chip (overdue, missing information), never red. */
  attention?: boolean;
  href?: string;
  linkLabel?: string;
}) {
  const u = urgency ? URG[urgency] : null;
  const chip = u ? u.chip : attention ? "bg-warning-bg text-warning border border-warning/30" : "bg-primary-tint text-primary";
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-[16px] border border-white/60 bg-card p-5 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)] transition-all duration-300 hover:shadow-[6px_6px_15px_0px_rgba(0,0,0,0.05),-6px_-6px_15px_0px_rgba(255,255,255,0.9)] hover:-translate-y-1">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-sm font-bold leading-5 text-muted">
          {u && <span className="mr-1.5 font-bold tracking-wide text-ink">{u.word}</span>}
          {label}
        </h2>
        <span className={`inline-flex size-9 shrink-0 items-center justify-center rounded-lg ${chip}`}>
          <Icon name={u ? u.icon : icon} size={18} />
        </span>
      </div>
      <p className="text-2xl font-bold leading-none tabular-nums sm:text-3xl text-ink">{value}</p>
      {(sub || href) && (
        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
          {sub && <span>{sub}</span>}
          {href && (
            <Link href={href} className="inline-flex min-h-8 items-center gap-1 font-bold text-primary underline-offset-4 hover:underline">
              {linkLabel ?? "View"} <Icon name="arrowRight" size={14} />
            </Link>
          )}
        </div>
      )}
    </section>
  );
}
