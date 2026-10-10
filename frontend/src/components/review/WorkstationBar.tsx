// Page header shared by the reviewer queue and the governance page: title, signed-in role, server time, actions,
// and a persistent facility-access line (GET /auth/me). The facility line is words plus an icon, never colour alone;
// "isolation off" uses the amber attention tone (a limitation to know about), not red.
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
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 space-y-1">
          <h1 className="flex items-center gap-2.5 text-2xl font-bold leading-tight tracking-tight sm:text-[1.75rem]">
            <span className="inline-flex size-9 items-center justify-center rounded-lg bg-primary-tint text-primary">
              <Icon name={icon} size={20} />
            </span>
            {title}
          </h1>
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
            <span className="font-bold text-ink">{roleText(me, meFailed)}</span>
            {meta && (
              <>
                <span aria-hidden="true">·</span>
                <span className="tabular-nums">{meta}</span>
              </>
            )}
          </p>
        </div>
        {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
      </div>
      {(me || meFailed) && <FacilityScopeLine me={me} />}
    </div>
  );
}

export function FacilityScopeLine({ me }: { me: AuthMe | null }) {
  const n = facilityNotice(me);
  if (n.kind === "open") {
    return (
      <p className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
        <Icon name="alert" size={16} className="mt-0.5" />
        <span className="font-bold">{n.text}</span>
      </p>
    );
  }
  return (
    <p className="flex items-start gap-2 text-sm text-muted">
      <Icon name={n.kind === "scoped" ? "building" : "info"} size={16} className="mt-0.5" />
      <span>{n.text}</span>
    </p>
  );
}

/** Secondary header action (button or link) on the light page surface. */
export const shellAction = "inline-flex min-h-11 items-center gap-2 rounded-lg border border-subtle bg-card px-4 text-sm font-bold text-ink shadow-card transition-colors duration-150 hover:border-strong hover:bg-surface-2";
