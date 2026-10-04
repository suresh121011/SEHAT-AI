// Small shared UI primitives on the design tokens (globals.css). 44px minimum targets; visible focus comes from
// globals.css; colour is never the only signal (every tone has an icon and words).
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { Icon, type IconName } from "@/components/Icon";

type Variant = "primary" | "secondary" | "danger" | "quiet";

const VARIANT: Record<Variant, string> = {
  primary: "bg-primary text-white hover:bg-primary-hover border border-primary",
  secondary: "bg-card text-primary border-2 border-primary hover:bg-primary-tint",
  danger: "bg-card text-error border-2 border-error hover:bg-error-bg",
  quiet: "bg-transparent text-primary underline underline-offset-4 hover:text-primary-hover border border-transparent",
};

export function buttonClass(variant: Variant = "primary", extra = "") {
  return `inline-flex min-h-11 items-center justify-center gap-2 rounded px-5 py-2 text-base font-bold transition-colors motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-55 ${VARIANT[variant]} ${extra}`;
}

export function Button({ variant = "primary", className = "", ...props }: ComponentProps<"button"> & { variant?: Variant }) {
  return <button type="button" {...props} className={buttonClass(variant, className)} />;
}

export function ButtonLink({ variant = "primary", className = "", ...props }: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link {...props} className={buttonClass(variant, className)} />;
}

export type Tone = "info" | "success" | "warning" | "error" | "consent" | "ai";

const TONE: Record<Tone, { cls: string; icon: IconName }> = {
  info: { cls: "bg-info-bg text-info border-info/30", icon: "info" },
  success: { cls: "bg-success-bg text-success border-success/30", icon: "check" },
  warning: { cls: "bg-warning-bg text-warning border-warning/40", icon: "alert" },
  error: { cls: "bg-error-bg text-error border-error/40", icon: "alert" },
  consent: { cls: "bg-consent-bg text-consent border-consent/30", icon: "shield" },
  ai: { cls: "bg-ai-bg text-ai border-ai border-dashed", icon: "sparkle" },
};

// role="status" for neutral updates; pass role="alert" only for errors that need immediate attention.
export function Notice({ tone = "info", title, children, role, className = "" }: { tone?: Tone; title?: ReactNode; children?: ReactNode; role?: "status" | "alert" | "note"; className?: string }) {
  const t = TONE[tone];
  return (
    <div role={role} className={`flex items-start gap-3 rounded-lg border px-4 py-3 text-base ${t.cls} ${className}`}>
      <Icon name={t.icon} size={20} className="mt-0.5" />
      <div className="min-w-0 space-y-1 text-ink [&_a]:text-primary">
        {title && <p className="font-bold">{title}</p>}
        {children}
      </div>
    </div>
  );
}

export function Card({ children, className = "", as: As = "section" }: { children: ReactNode; className?: string; as?: "section" | "div" | "article" | "aside" }) {
  return <As className={`rounded-lg border border-subtle bg-card p-4 sm:p-5 ${className}`}>{children}</As>;
}

export function Field({ id, label, hint, error, children }: { id: string; label: ReactNode; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <div className={`space-y-1 ${error ? "border-l-4 border-error pl-3" : ""}`}>
      <label htmlFor={id} className="block font-bold text-ink">
        {label}
      </label>
      {hint && (
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="font-bold text-error">
          <span className="sr-only">Error: </span>
          {error}
        </p>
      )}
      {children}
    </div>
  );
}

export const inputClass = "block min-h-11 w-full rounded border-2 border-line bg-card px-3 py-2 text-base text-ink";

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-muted">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" className="animate-spin motion-reduce:animate-none">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeDasharray="42 60" strokeLinecap="round" />
      </svg>
      {label}
    </span>
  );
}
