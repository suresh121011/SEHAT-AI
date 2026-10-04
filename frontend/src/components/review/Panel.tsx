// Shared panel chrome for the reviewer screens: one card surface, one heading scale (16 px bold, 16 px icon), an
// optional right-hand aside (provenance badge, meta text). Extracted from markup repeated across the workstation.
import type { ReactNode } from "react";

import { Icon, type IconName } from "@/components/Icon";

export function SectionCard({ id, title, icon, aside, children, className = "", tone = "card" }: {
  id: string;
  title: ReactNode;
  icon?: IconName;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: "card" | "plain";
}) {
  return (
    <section aria-labelledby={id} className={`space-y-3 rounded-lg border border-subtle p-4 ${tone === "card" ? "bg-card" : ""} ${className}`}>
      <PanelHeader id={id} title={title} icon={icon} aside={aside} />
      {children}
    </section>
  );
}

export function PanelHeader({ id, title, icon, aside }: { id: string; title: ReactNode; icon?: IconName; aside?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <h3 id={id} className="flex items-center gap-1.5 text-base font-bold leading-6">
        {icon && <Icon name={icon} size={16} />}
        {title}
      </h3>
      {aside}
    </div>
  );
}

/** Small uppercase label above a value ("Rules-engine result"). */
export function Eyebrow({ icon, children }: { icon?: IconName; children: ReactNode }) {
  return (
    <p className="flex items-center gap-1 text-xs font-bold uppercase tracking-wide text-muted">
      {icon && <Icon name={icon} size={14} />}
      {children}
    </p>
  );
}
