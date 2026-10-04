// Dark workstation bar shared by the reviewer queue and the governance page: title, signed-in role, server time,
// actions, and a persistent facility-access line (GET /auth/me). The facility line is words plus an icon, never
// colour alone; "isolation off" uses the amber attention tone (a limitation to know about), not red.
import type { ReactNode } from "react";

import { Icon, type IconName } from "@/components/Icon";
import { facilityNotice, type AuthMe } from "@/lib/facilityScope";

const ROLE_TEXT: Record<string, string> = { supervisor: "Supervisor · read only", medical_officer: "Medical officer", admin: "Admin" };

export function roleText(me: AuthMe | null, failed: boolean): string {
  if (me) return ROLE_TEXT[me.role] ?? `Role: ${me.role}`;
  return failed ? "Role unknown (session check failed)" : "Checking role…";
}

export function WorkstationBar({ title, icon, me, meFailed, meta, children }: {
  title: string;
  icon: IconName;
  me: AuthMe | null;
  meFailed: boolean;
  meta?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="on-shell bg-shell text-shell-ink">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
        <h1 className="flex items-center gap-2 text-lg font-bold leading-7">
          <Icon name={icon} size={20} /> {title}
        </h1>
        <span className="text-sm text-shell-muted">{roleText(me, meFailed)}</span>
        {meta && <span className="text-sm tabular-nums text-shell-muted">{meta}</span>}
        {children && <div className="ml-auto flex flex-wrap items-center gap-2">{children}</div>}
      </div>
      {(me || meFailed) && <FacilityScopeLine me={me} />}
    </div>
  );
}

export function FacilityScopeLine({ me }: { me: AuthMe | null }) {
  const n = facilityNotice(me);
  if (n.kind === "open") {
    return (
      <p className="flex items-start gap-2 border-t border-shell-muted/30 bg-warning-bg px-4 py-1.5 text-sm text-warning">
        <Icon name="alert" size={16} className="mt-0.5" />
        <span className="font-bold">{n.text}</span>
      </p>
    );
  }
  return (
    <p className="flex items-start gap-2 border-t border-shell-muted/30 px-4 py-1.5 text-sm text-shell-muted">
      <Icon name={n.kind === "scoped" ? "building" : "info"} size={16} className="mt-0.5" />
      <span>{n.text}</span>
    </p>
  );
}

/** Shell-styled action button/link class (white focus ring comes from `.on-shell` in globals.css). */
export const shellAction = "inline-flex min-h-10 items-center gap-1.5 rounded border border-shell-muted/60 px-3 text-sm font-bold hover:bg-white/10";
