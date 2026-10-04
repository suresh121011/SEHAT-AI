"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { Icon } from "@/components/Icon";
import { ButtonLink, Notice } from "@/components/ui";
import { type Availability, type ConsentState, STEPS, type StepId, availability, hrefFor, nextStep, stepIndex } from "@/lib/steps";

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
    <nav aria-label="Intake steps" className="no-print">
      <p className="text-sm font-bold text-muted md:hidden">
        Step {idx + 1} of {STEPS.length}: {STEPS[idx].label}
      </p>
      <ol className="hidden gap-1 md:flex">
        {STEPS.map((s, i) => {
          const isCurrent = s.id === current;
          const avail = caseId || s.id === "case" ? availability(s.id, role, triage) : "needs_consent";
          const reachable = avail === "open" && (caseId || s.id === "case");
          const done = i < idx;
          const body = (
            <span className="flex flex-col gap-1">
              <span className={`h-1.5 rounded-full ${isCurrent ? "bg-primary" : done ? "bg-primary/60" : "bg-subtle"}`} aria-hidden="true" />
              <span className={`flex items-center gap-1 text-sm ${isCurrent ? "font-bold text-primary" : "text-muted"}`}>
                <span aria-hidden="true">{i + 1}.</span> {s.label}
                {done && <span className="sr-only"> (earlier step)</span>}
                {avail !== "open" && <span className="sr-only"> ({AVAIL_TEXT[avail]})</span>}
              </span>
            </span>
          );
          return (
            <li key={s.id} className="min-w-0 flex-1" aria-current={isCurrent ? "step" : undefined}>
              {reachable && !isCurrent ? (
                <Link href={hrefFor(s.id, caseId)} className="block rounded py-1 hover:[&_span]:text-ink">
                  {body}
                </Link>
              ) : (
                <span className="block py-1">{body}</span>
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
      <div className="flex items-start gap-3 rounded-lg border-2 border-secondary bg-info-bg px-4 py-3" role="note">
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
      <div className="flex items-start gap-3 rounded-lg border border-subtle bg-card px-4 py-3" role="note">
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
  const workerSteps = role === "patient" && caseId && triage === "granted" && !next && step !== "case" && step !== "consent";

  return (
    <div className={`mx-auto space-y-5 ${wide ? "max-w-5xl" : "max-w-3xl"}`}>
      <DemoBanner />
      <Stepper current={step} caseId={caseId} role={role} triage={triage} />
      <AssistedBanner role={role} />

      <header className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 ref={heading} tabIndex={-1} className="text-3xl font-bold leading-tight text-ink outline-none">
            {STEPS[idx].title}
          </h1>
          {token && (
            <p className="text-sm text-muted">
              Case <span className="font-mono">{token}</span>
              {scenario && <> · {SCENARIO_LABEL[scenario] ?? scenario}</>}
            </p>
          )}
        </div>
        {intro && <div className="max-w-prose text-lg text-muted">{intro}</div>}
      </header>

      {children}

      {workerSteps && (
        <Notice tone="info" title="Next: hand the device to the health worker">
          <p>Reports, follow-up questions and the triage form are completed by the health worker who started this case with you.</p>
        </Notice>
      )}

      <nav aria-label="Step navigation" className="no-print flex flex-wrap items-center justify-between gap-3 border-t border-subtle pt-4">
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
                <span className="inline-flex min-h-11 cursor-not-allowed items-center gap-2 rounded bg-subtle px-5 py-2 font-bold text-muted" aria-disabled="true">
                  Continue: {STEPS[stepIndex(next)].label} <Icon name="arrowRight" />
                </span>
                {nextHint && <p className="text-sm text-muted">{nextHint}</p>}
              </>
            )}
          </div>
        )}
      </nav>
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
