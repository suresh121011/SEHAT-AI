import { Icon, type IconName } from "@/components/Icon";
import { Notice } from "@/components/ui";

// Displays the rules engine's own result (POST /cases/{id}/triage). Nothing here computes or changes urgency.
// Urgency is never colour alone: icon shape + words + colour (NHS care-card pattern).

export type TriageResult = {
  urgency: "RED" | "YELLOW" | "GREEN";
  determination: "complete" | "insufficient_data" | "outside_validated_population";
  needs_human_review: boolean;
  triggered_rules: { rule_id: string; urgency: string; reason: string; source: string }[];
  missing_fields: string[];
  advisories: { code: string; message: string }[];
  engine_version: string;
  ruleset_version: string;
  disclaimer: string;
};

export type Counterfactuals = { status: string; reason?: string; counterfactuals?: { if_changed: Record<string, unknown>; then_urgency: string; rule_ids: string[] }[] };

const URGENCY: Record<TriageResult["urgency"], { title: string; text: string; icon: IconName; cls: string }> = {
  RED: { title: "RED: immediate attention", text: "Needs to be seen immediately.", icon: "octagon", cls: "bg-urg-red text-urg-red-ink" },
  YELLOW: { title: "YELLOW: urgent review", text: "Needs prompt review by a clinician.", icon: "alert", cls: "bg-urg-yellow text-urg-yellow-ink" },
  GREEN: { title: "GREEN: routine queue", text: "Routine queue. A clinician still reviews this; it does not mean the patient is well.", icon: "circle", cls: "bg-urg-green text-urg-green-ink border border-urg-green-ink/30" },
};

const DETERMINATION: Record<TriageResult["determination"], string> = {
  complete: "The rules had the information they need.",
  insufficient_data: "Some information is missing, so the result needs human review. Missing values are never treated as normal.",
  outside_validated_population: "This patient is outside the group these rules were written for, so the result needs human review.",
};

const FIELD_WORDS: Record<string, string> = {
  "vitals.resp_rate": "breathing rate",
  "vitals.spo2": "oxygen saturation",
  "vitals.pulse": "pulse",
  "vitals.sbp": "systolic blood pressure",
  "vitals.temp_c": "temperature",
  "vitals.consciousness": "level of alertness",
  "vitals.on_supplemental_oxygen": "whether on oxygen",
  red_flag_screen_completed: "red-flag screen",
  age_years: "age",
};

function words(field: string) {
  return FIELD_WORDS[field] ?? field.replace(/^vitals\./, "").replaceAll("_", " ");
}

function describeChange(c: Record<string, unknown>): string {
  const field = String(c.field ?? "");
  if (field === "red_flags_present") return `if "${String(c.remove).replaceAll("_", " ")}" had not been ticked`;
  if (field === "red_flag_screen_completed") return "if the red-flag screen had not been completed";
  return `if ${words(field)} were ${String(c.to)} instead of ${String(c.from)}`;
}

export function UrgencyResult({ result, counterfactuals }: { result: TriageResult; counterfactuals: Counterfactuals | "loading" | "unavailable" }) {
  const u = URGENCY[result.urgency];
  return (
    <section aria-labelledby="result-heading" className="space-y-4">
      <div className={`flex items-start gap-4 rounded-lg p-5 ${u.cls}`}>
        <Icon name={u.icon} size={40} />
        <div>
          <h2 id="result-heading" className="text-2xl font-bold">
            {u.title}
          </h2>
          <p className="text-lg">{u.text}</p>
          <p className="mt-1 text-sm">Decided by fixed rules, not by AI.</p>
        </div>
      </div>

      <Notice tone={result.needs_human_review ? "warning" : "consent"} title={result.needs_human_review ? "Needs human review, not signed off yet" : "Not signed off yet"}>
        {result.needs_human_review && <p>{DETERMINATION[result.determination]}</p>}
        <p>
          {result.disclaimer} A medical officer reviews and signs off this result on the reviewer dashboard.
        </p>
      </Notice>

      <div className="rounded-lg border border-subtle bg-card p-5">
        <h3 className="text-xl font-bold">Why this result</h3>
        {result.triggered_rules.length === 0 ? (
          <p className="mt-2 text-muted">No rule raised the urgency.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {result.triggered_rules.map((r) => (
              <li key={r.rule_id} className="border-l-4 border-primary pl-3">
                <p className="font-bold">{r.reason}</p>
                <details className="text-sm text-muted">
                  <summary className="cursor-pointer py-1">Rule details for the clinician</summary>
                  <p>
                    {r.rule_id} ({r.urgency}). Source: {r.source}
                  </p>
                </details>
              </li>
            ))}
          </ul>
        )}
        {result.missing_fields.length > 0 && (
          <p className="mt-3">
            <strong>Not provided:</strong> {result.missing_fields.map(words).join(", ")}.
          </p>
        )}
        {result.advisories.length > 0 && (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
            {result.advisories.map((a) => (
              <li key={a.code}>{a.message}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-lg border border-subtle bg-card p-5" aria-live="polite">
        <h3 className="text-xl font-bold">What would change this result?</h3>
        <p className="text-sm text-muted">The rules re-run with one value changed. An explanation only, not part of the triage record, and not advice.</p>
        {counterfactuals === "loading" && <p className="mt-2 text-muted">Working this out…</p>}
        {(counterfactuals === "unavailable" || (typeof counterfactuals === "object" && counterfactuals.status !== "computed")) && <p className="mt-2 text-muted">Not available for this result.</p>}
        {typeof counterfactuals === "object" && counterfactuals.status === "computed" && (counterfactuals.counterfactuals?.length ?? 0) === 0 && (
          <p className="mt-2">No single change to one value would change this result.</p>
        )}
        {typeof counterfactuals === "object" && counterfactuals.status === "computed" && (counterfactuals.counterfactuals?.length ?? 0) > 0 && (
          <ul className="mt-2 space-y-1">
            {counterfactuals.counterfactuals!.map((c, i) => (
              <li key={i}>
                {describeChange(c.if_changed)}, the rules would give <strong>{c.then_urgency}</strong>.
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
