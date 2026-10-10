"use client";

// "What would change the result": the existing endpoint re-runs the deterministic rules engine on the run's stored
// input with ONE value changed (app/rules/counterfactual.py). Read only: a GET that writes nothing, so nothing here
// can touch the patient's record. Shown on a hatched surface with an outlined urgency word so it never looks like
// the real result. No threshold or result is computed in the browser.
import { useEffect, useState } from "react";

import { Icon } from "@/components/Icon";
import { HypotheticalUrgency, UrgencyBadge } from "@/components/review/Badges";
import { Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { describeChange, type CounterfactualChange, type Urgency } from "@/lib/review";

type Resp = { status: "computed" | "unavailable"; reason?: string; urgency?: Urgency; counterfactuals?: { if_changed: CounterfactualChange; then_urgency: string; rule_ids: string[] }[]; note?: string };

export function CounterfactualPanel({ caseId, runId, actual, canCompute }: { caseId: string; runId: string; actual: Urgency; canCompute: boolean }) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ok"; data: Resp } | { kind: "error"; msg: string }>({ kind: "loading" });

  useEffect(() => {
    if (!canCompute) return;
    let live = true;
    setState({ kind: "loading" });
    api
      .get<Resp>(`cases/${caseId}/triage/runs/${runId}/counterfactuals`)
      .then((data) => live && setState({ kind: "ok", data }))
      .catch((e) => live && setState({ kind: "error", msg: e instanceof ApiError && e.code === "CONSENT_REQUIRED" ? "Consent is not in effect, so this cannot be computed." : "Could not be computed right now." }));
    return () => {
      live = false;
    };
  }, [caseId, runId, canCompute]);

  return (
    <section aria-labelledby="cf-heading" className="hypothetical space-y-2 rounded-lg p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-hypo-chip px-1.5 py-0.5 text-xs font-bold uppercase tracking-wider text-white">Hypothetical</span>
        <h3 id="cf-heading" className="text-base font-bold">
          What-if: what would change the rules’ answer? (not a real result)
        </h3>
      </div>
      <p className="text-sm text-ink">
        <strong>Not the actual triage result.</strong> The rules engine re-runs with one recorded value changed. Exploring this never changes the
        patient’s record. Explains the rules as written; not clinical advice.
      </p>
      <p className="flex flex-wrap items-center gap-2 text-sm">
        Actual result: <UrgencyBadge urgency={actual} size="sm" />
      </p>

      {!canCompute && <p className="text-sm text-muted">Available to the medical officer reviewing the case (needs triage consent).</p>}
      {canCompute && state.kind === "loading" && <Spinner label="Re-running the rules…" />}
      {canCompute && state.kind === "error" && <p className="text-sm text-muted">{state.msg}</p>}
      {canCompute && state.kind === "ok" && state.data.status !== "computed" && (
        <p className="text-sm text-muted">Unavailable: {state.data.reason ?? "this run has no stored input"}. No result is simulated.</p>
      )}
      {canCompute && state.kind === "ok" && state.data.status === "computed" && (
        <>
          {(state.data.counterfactuals?.length ?? 0) === 0 ? (
            <p className="text-sm">No single change to one recorded value would change this result.</p>
          ) : (
            <ul className="space-y-2">
              {state.data.counterfactuals!.map((c, i) => (
                <li key={i} className="rounded-lg border border-hypo-line bg-card/90 p-2 text-sm">
                  <p className="flex items-start gap-1.5">
                    <Icon name="question" size={16} className="mt-0.5 text-muted" />
                    <span>{describeChange(c.if_changed)},</span>
                  </p>
                  <p className="mt-1 flex flex-wrap items-center gap-1.5 pl-5">
                    the rules would give <HypotheticalUrgency urgency={c.then_urgency} />
                  </p>
                  {c.rule_ids.length > 0 && <p className="mt-1 pl-5 font-mono text-xs text-muted">Rules: {c.rule_ids.join(", ")}</p>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
