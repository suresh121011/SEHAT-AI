"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { Icon } from "@/components/Icon";
import { ButtonLink, Notice } from "@/components/ui";
import { type Availability, type ConsentState, STEPS, type StepId, availability, handoffDue, hrefFor, nextStep, stepIndex } from "@/lib/steps";

const SCENARIO_LABEL: Record<string, string> = {
  opd: "Outpatient visit",
  maternal: "Pregnancy care",
  chronic_ncd: "Long-term condition",
  health_camp: "Health camp",
  campus_fever: "Campus fever",
  occupational: "Work-related",
  referral: "Referral",
};

const AVAIL_TEXT: Record<Availability, string> = {
  open: "",
  needs_consent: "needs consent first",
  health_worker_only: "done by the health worker",
};

function Stepper({ current, caseId, role, triage }: { current: StepId; caseId: string | null; role: string | null; triage: ConsentState | null }) {
  const idx = stepIndex(current);
  return (
    <nav aria-label="Intake steps" className="no-print rounded-[16px] border border-white/60 bg-card p-4 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)] sm:px-5">
      <div className="md:hidden">
        <p className="flex items-baseline justify-between gap-2 text-sm">
          <span className="font-bold text-primary">
            Step {idx + 1} of {STEPS.length}
          </span>
          <span className="font-bold text-ink">{STEPS[idx].label}</span>
        </p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
          <div className="h-full rounded-full bg-primary" style={{ width: `${((idx + 1) / STEPS.length) * 100}%` }} />
        </div>
      </div>
      <ol className="hidden md:flex">
        {STEPS.map((s, i) => {
          const isCurrent = s.id === current;
          const avail = caseId || s.id === "case" ? availability(s.id, role, triage) : "needs_consent";
          const reachable = avail === "open" && (caseId || s.id === "case");
          const done = i < idx;
          const body = (
            <span className="flex flex-col items-center gap-1.5 text-center">
              <span
                className={`relative z-10 inline-flex size-8 items-center justify-center rounded-full border text-sm font-bold ${
                  isCurrent ? "border-primary bg-primary text-white shadow-[0_0_0_4px_var(--primary-tint)]" : done ? "border-primary bg-primary-tint text-primary" : "border-strong bg-card text-muted"
                }`}
                aria-hidden="true"
              >
                {done ? <Icon name="check" size={16} /> : avail !== "open" && !isCurrent ? <Icon name="lock" size={14} /> : i + 1}
              </span>
              <span className={`text-sm leading-tight ${isCurrent ? "font-bold text-ink" : done ? "text-ink" : "text-muted"}`}>
                {s.label}
                {done && <span className="sr-only"> (earlier step)</span>}
                {avail !== "open" && <span className="sr-only"> ({AVAIL_TEXT[avail]})</span>}
              </span>
            </span>
          );
          return (
            <li key={s.id} className="relative min-w-0 flex-1" aria-current={isCurrent ? "step" : undefined}>
              {i > 0 && <span className={`absolute right-1/2 top-4 h-0.5 w-full -translate-y-1/2 ${i <= idx ? "bg-primary/60" : "bg-subtle"}`} aria-hidden="true" />}
              {reachable && !isCurrent ? (
                <Link href={hrefFor(s.id, caseId)} className="relative block rounded-lg py-1 hover:[&_span]:text-primary">
                  {body}
                </Link>
              ) : (
                <span className="relative block py-1">{body}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function AssistedBanner({ role }: { role: string | null }) {
  if (role === "anm") {
    return (
      <div className="flex items-start gap-3 rounded-[12px] border border-white/60 bg-surface-2 px-4 py-3 shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02),inset_-1px_-1px_3px_rgba(255,255,255,0.7)]" role="note">
        <Icon name="people" size={22} className="mt-0.5 text-secondary" />
        <p className="text-ink">
          <strong>Assisted mode: health worker with the patient.</strong> You operate the screen together. Read each question
          aloud, enter what the patient says, and check values with them. Values you enter or confirm are recorded as yours.
        </p>
      </div>
    );
  }
  if (role === "patient") {
    return (
      <div className="flex items-start gap-3 rounded-[12px] border border-white/60 bg-surface-2 px-4 py-3 shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02),inset_-1px_-1px_3px_rgba(255,255,255,0.7)]" role="note">
        <Icon name="people" size={22} className="mt-0.5 text-muted" />
        <p className="text-ink">
          <strong>You are using SEHAT AI yourself.</strong> A health worker will check everything with you and finish the steps
          you cannot do here.
        </p>
      </div>
    );
  }
  return null;
}

export function IntakeShell({
  step,
  caseId,
  role,
  triage,
  token,
  scenario,
  intro,
  children,
  nextReady = true,
  nextHint,
  wide = false,
}: {
  step: StepId;
  caseId: string | null;
  role: string | null;
  triage: ConsentState | null;
  token?: string;
  scenario?: string;
  intro?: ReactNode;
  children: ReactNode;
  nextReady?: boolean;
  nextHint?: string;
  wide?: boolean;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), [step]);
  const idx = stepIndex(step);
  const prev = idx > 0 ? STEPS[idx - 1].id : null;
  const next = nextStep(step, role, triage);
  const handoff = !!caseId && handoffDue(step, role, triage);

  return (
    <div className={`mx-auto space-y-5 ${wide ? "max-w-6xl" : "max-w-4xl"}`}>
      <header className="space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p className="text-sm font-bold text-primary">
            New patient intake<span className="hidden md:inline"> · Step {idx + 1} of {STEPS.length}</span>
          </p>
          {token && (
            <p className="inline-flex items-center gap-2 rounded-full border border-white/60 bg-surface-1 px-3 py-1 text-sm text-muted shadow-[inset_1px_1px_3px_rgba(0,0,0,0.02),inset_-1px_-1px_3px_rgba(255,255,255,0.7)]">
              <Icon name="clipboard" size={14} className="text-primary" />
              Case <span className="font-mono font-bold text-ink">{token}</span>
              {scenario && <span className="hidden sm:inline">· {SCENARIO_LABEL[scenario] ?? scenario}</span>}
            </p>
          )}
        </div>
        <h1 ref={heading} tabIndex={-1} className="text-2xl font-bold leading-tight tracking-tight text-ink outline-none sm:text-[1.75rem]">
          {STEPS[idx].title}
        </h1>
        {intro && <div className="max-w-prose text-base text-muted sm:text-lg">{intro}</div>}
      </header>
      <Stepper current={step} caseId={caseId} role={role} triage={triage} />
      <div className="space-y-3">
        <DemoBanner />
        <AssistedBanner role={role} />
      </div>

      {children}

      {handoff && (
        <Notice tone="warning" title="Your part is done. Nothing has been sent for triage yet.">
          <p>
            Reports, follow-up questions and the triage form are done by a health worker, who checks every value with you.
            {token ? (
              <>
                {" "}Show them this case code: <span className="font-mono text-lg font-bold">{token}</span>
              </>
            ) : null}
          </p>
          <p className="mt-2">
            The health worker signs in on their own account and chooses &ldquo;Continue a patient&apos;s case&rdquo;. Your voice recording and
            body map are already saved with the case. Log out before you hand over this device.
          </p>
        </Notice>
      )}

      {((prev && (caseId || prev === "case")) || (next && caseId)) && (
      <nav aria-label="Step navigation" className="no-print flex flex-wrap items-center justify-between gap-3 rounded-[16px] border border-white/60 bg-card p-4 shadow-[4px_4px_10px_0px_rgba(0,0,0,0.03),-4px_-4px_10px_0px_rgba(255,255,255,0.8)]">
        {prev && (caseId || prev === "case") ? (
          <ButtonLink href={hrefFor(prev, caseId)} variant="quiet">
            <Icon name="arrowLeft" /> Back: {STEPS[stepIndex(prev)].label}
          </ButtonLink>
        ) : (
          <span />
        )}
        {next && caseId && (
          <div className="flex flex-col items-end gap-1">
            {nextReady ? (
              <ButtonLink href={hrefFor(next, caseId)}>
                Continue: {STEPS[stepIndex(next)].label} <Icon name="arrowRight" />
              </ButtonLink>
            ) : (
              <>
                <span className="inline-flex min-h-11 cursor-not-allowed items-center gap-2 rounded-[12px] bg-surface-2 px-5 py-2 font-bold text-muted shadow-[inset_1px_1px_3px_rgba(0,0,0,0.05),inset_-1px_-1px_3px_rgba(255,255,255,0.5)]" aria-disabled="true">
                  Continue: {STEPS[stepIndex(next)].label} <Icon name="arrowRight" />
                </span>
                {nextHint && <p className="text-sm text-muted">{nextHint}</p>}
              </>
            )}
          </div>
        )}
      </nav>
      )}
    </div>
  );
}

export function CaseNotFound() {
  return (
    <div className="mx-auto max-w-xl space-y-4">
      <DemoBanner />
      <Notice tone="error" role="alert" title="Case not found">
        <p>This case does not exist, or it was started from a different account.</p>
      </Notice>
      <ButtonLink href="/intake">Start a new case</ButtonLink>
    </div>
  );
}
