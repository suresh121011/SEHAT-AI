// Small shared UI primitives on the design tokens (globals.css). 44px minimum targets; visible focus comes from
// globals.css; colour is never the only signal (every tone has an icon and words).
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { Icon, type IconName } from "@/components/Icon";

type Variant = "primary" | "secondary" | "danger" | "success" | "quiet";

const VARIANT: Record<Variant, string> = {
  primary: "bg-[#0891B2] text-white hover:bg-[#06b6d4] border border-[#0891B2] shadow-[4px_4px_10px_rgba(8,145,178,0.2),-4px_-4px_10px_rgba(255,255,255,0.8)] active:shadow-[inset_2px_2px_5px_rgba(0,0,0,0.1)]",
  secondary: "bg-card text-[#164E63] hover:bg-[#e8f1f6] shadow-[4px_4px_10px_rgba(165,243,252,0.4),-4px_-4px_10px_rgba(255,255,255,0.9)] active:shadow-[inset_2px_2px_5px_rgba(165,243,252,0.4)]",
  danger: "bg-card text-error border-2 border-error hover:bg-error-bg shadow-[4px_4px_10px_rgba(220,38,38,0.1),-4px_-4px_10px_rgba(255,255,255,0.9)] active:shadow-[inset_2px_2px_5px_rgba(220,38,38,0.1)]",
  success: "bg-success text-white border border-success hover:bg-success/90 shadow-[4px_4px_10px_rgba(16,185,129,0.2),-4px_-4px_10px_rgba(255,255,255,0.8)] active:shadow-[inset_2px_2px_5px_rgba(0,0,0,0.1)]",
  quiet: "bg-transparent text-[#0891B2] underline underline-offset-4 hover:text-[#06b6d4]",
};

export function buttonClass(variant: Variant = "primary", extra = "") {
  return `inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-5 py-2 text-base font-semibold transition-all duration-150 motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-55 hover:-translate-y-[1px] active:translate-y-[1px] ${VARIANT[variant]} ${extra}`;
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
    <div role={role} className={`flex items-start gap-3 rounded-[12px] border border-white/60 px-4 py-3 text-base shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02),inset_-1px_-1px_3px_rgba(255,255,255,0.7)] ${t.cls} ${className}`}>
      <Icon name={t.icon} size={20} className="mt-0.5" />
      <div className="min-w-0 space-y-1 text-ink [&_a]:text-primary">
        {title && <p className="font-bold">{title}</p>}
        {children}
      </div>
    </div>
  );
}

export function Card({ children, className = "", as: As = "section" }: { children: ReactNode; className?: string; as?: "section" | "div" | "article" | "aside" | "li" }) {
  return <As className={`rounded-[16px] border border-white/60 bg-card p-6 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)] transition-all duration-300 hover:shadow-[6px_6px_15px_0px_rgba(0,0,0,0.05),-6px_-6px_15px_0px_rgba(255,255,255,0.9)] hover:-translate-y-1 ${className}`}>{children}</As>;
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

export const inputClass = "block min-h-11 w-full rounded-[12px] border border-white/60 bg-surface-1 px-3 py-2 text-base text-ink shadow-[inset_2px_2px_5px_rgba(0,0,0,0.05),inset_-2px_-2px_5px_rgba(255,255,255,0.7)] placeholder:text-muted/80 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-shadow";

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

/** Page title block used at the top of every screen: optional eyebrow, one h1, a short description, and actions
 * that wrap below the title on small screens. */
export function PageHeader({ eyebrow, title, description, actions, headingRef }: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  headingRef?: React.Ref<HTMLHeadingElement>;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0 space-y-1">
        {eyebrow && <p className="text-sm font-bold text-primary">{eyebrow}</p>}
        <h1 ref={headingRef} tabIndex={headingRef ? -1 : undefined} className="text-2xl font-bold leading-tight tracking-tight text-ink outline-none sm:text-[1.75rem]">
          {title}
        </h1>
        {description && <div className="max-w-prose text-base text-muted">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
