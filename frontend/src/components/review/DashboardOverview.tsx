import { useMemo } from "react";
import { MetricCard } from "@/components/MetricCard";
import type { Queue } from "@/lib/review";
import { Icon } from "@/components/Icon";

export function DashboardOverview({ queue }: { queue: Queue }) {
  const stats = useMemo(() => {
    let red = 0;
    let yellow = 0;
    let green = 0;
    for (const item of queue.items) {
      if (item.rules_urgency === "RED") red++;
      else if (item.rules_urgency === "YELLOW") yellow++;
      else if (item.rules_urgency === "GREEN") green++;
    }
    return {
      total: queue.items.length,
      red,
      yellow,
      green,
    };
  }, [queue.items]);

  const date = new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });

  return (
    <div className="p-6 space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold text-ink">Welcome back, Dr. Smith</h1>
        <p className="text-muted">{date} • All systems operational</p>
      </header>

      <section aria-label="Priority Overview" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Total Cases"
          value={stats.total}
          icon="list"
          sub={<span className="text-urg-green-ink font-medium">↑ 12% from yesterday</span>}
        />
        <MetricCard
          label="Priority"
          value={stats.red}
          urgency="RED"
          sub={<span className="text-urg-red-ink font-medium">Action required</span>}
        />
        <MetricCard
          label="Priority"
          value={stats.yellow}
          urgency="YELLOW"
          sub={<span className="text-muted">Monitor closely</span>}
        />
        <MetricCard
          label="Priority"
          value={stats.green}
          urgency="GREEN"
          sub={<span className="text-muted">Standard queue</span>}
        />
      </section>

      <section aria-label="Recent Activity" className="space-y-4">
        <h2 className="text-lg font-bold text-ink pl-1">Recent Activity</h2>
        <div className="bg-card rounded-[16px] border border-white/60 p-5 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)]">
          <ul className="space-y-4">
            {[
              { time: "10:42 AM", title: "Case escalated to RED", desc: "Automated triage marked #CS-892 as RED due to elevated heart rate.", icon: "alert" as const, color: "text-urg-red-ink bg-urg-red" },
              { time: "09:15 AM", title: "Referral completed", desc: "Patient #PT-442 successfully referred to Cardiology.", icon: "check" as const, color: "text-urg-green-ink bg-urg-green border border-urg-green-ink/30" },
              { time: "08:30 AM", title: "New intake received", desc: "Voice intake processed and evidence extracted for #CS-893.", icon: "document" as const, color: "text-primary bg-primary-tint" },
            ].map((act, i) => (
              <li key={i} className="flex gap-4">
                <div className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full ${act.color}`}>
                  <Icon name={act.icon} size={16} />
                </div>
                <div>
                  <p className="text-sm font-bold text-ink">{act.title}</p>
                  <p className="text-sm text-muted">{act.desc}</p>
                  <p className="text-xs text-muted mt-1">{act.time}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}
