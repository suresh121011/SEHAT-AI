"""Deterministic triage pipeline. Pure: no I/O, no network, no LLM.

validate (Pydantic) -> derive -> ATP -> scenario rules -> NEWS2 -> qSOFA -> completeness gate -> aggregate
Urgency is the maximum of every triggered rule. GREEN is never a default: it must be earned by
complete data and a completed red-flag screen (see docs/10 ADR-4).
"""

from datetime import datetime, timezone
from typing import Literal

from app.rules import news2, qsofa
from app.rules.atp import ATP_REQUIRED_VITALS, ATP_RULES
from app.rules.models import (
    Advisory,
    FinalUrgency,
    News2Result,
    QsofaResult,
    Scenario,
    Scores,
    TriageInput,
    TriageResult,
    TriggeredRule,
)
from app.rules.registry import Context, Evidence, ev, hit, run_rules
from app.rules.scenarios import get_pack
from app.rules.urgency import Urgency, max_urgency

ENGINE_VERSION = "1.0.0"
RULESET_VERSION = "1.0.0"  # bump on any threshold/rule change

Determination = Literal["complete", "insufficient_data", "outside_validated_population"]

ATP_MIN_AGE = 14  # ATP_2022 validation population
ADULT_SCORE_MIN_AGE = 16  # RCP NEWS2; qSOFA is defined for adults


# ── SEHAT_POLICY mappings of scores to urgency ───────────────────────────


_NEWS2_THRESHOLDS = {
    "high": ">=7 (RCP high clinical risk)",
    "medium": "5-6 (RCP medium clinical risk)",
    "low_medium": "a single parameter scoring 3 (RCP low-medium clinical risk)",
}


def _news2_rule(result: News2Result) -> TriggeredRule | None:
    if result.status == "not_applicable" or result.band in (None, "low"):
        return None
    value = result.total if result.status == "complete" else f">={result.partial_total} (lower bound, incomplete)"
    return hit(
        f"NEWS2_{result.band.upper()}",
        "news2",
        Urgency.RED if result.band == "high" else Urgency.YELLOW,
        f"NEWS2 {result.band.replace('_', '-')} clinical risk (RCP Chart 2); SEHAT maps high->RED, medium and low-medium->YELLOW",
        "RCP_NEWS2_2017",
        {"news2": ev(value, _NEWS2_THRESHOLDS[result.band])},
    )


def _qsofa_rule(result: QsofaResult) -> TriggeredRule | None:
    if not result.positive:
        return None
    return hit(
        "QSOFA_POSITIVE",
        "qsofa",
        Urgency.YELLOW,
        "qSOFA >=2 with suspected infection: positive screen, not a diagnosis (SEHAT maps to YELLOW minimum)",
        "SEPSIS3_2016",
        {"qsofa": ev(result.total, ">=2 of: RR >=22, altered mentation, SBP <=100")},
    )


def _floor_rule(rule_id: str, reason: str, evidence: Evidence) -> TriggeredRule:
    return hit(rule_id, "safety_floor", Urgency.YELLOW, reason, "SEHAT_POLICY", evidence)


# ── Pipeline ─────────────────────────────────────────────────────────────


def _score_news2(data: TriageInput, uses_news2: bool) -> News2Result:
    if not uses_news2:
        return news2.not_applicable("scenario does not use NEWS2")
    if data.pregnant:
        return news2.not_applicable("NEWS2 is not validated in pregnancy (RCP)")
    if data.age_years is None:
        return news2.not_applicable("age unknown")
    if data.age_years < ADULT_SCORE_MIN_AGE:
        return news2.not_applicable("NEWS2 is not validated under 16 years (RCP)")
    return news2.score_news2(data.vitals)


def _score_qsofa(data: TriageInput) -> QsofaResult:
    if not data.suspected_infection:
        return qsofa.not_applicable("infection not suspected")
    if data.age_years is None or data.age_years < ADULT_SCORE_MIN_AGE:
        return qsofa.not_applicable("qSOFA is defined for adults")
    return qsofa.score_qsofa(data.vitals)


def _missing_fields(data: TriageInput, ctx: Context, news2_result: News2Result, pack) -> list[str]:
    missing: list[str] = []
    if data.age_years is None:
        missing.append("age_years")
    if not data.red_flag_screen_completed:
        missing.append("red_flag_screen_completed")
    for name in ATP_REQUIRED_VITALS:
        if getattr(data.vitals, name) is None:
            missing.append(f"vitals.{name}")
    if news2_result.status == "incomplete" and data.vitals.on_supplemental_oxygen is None:
        missing.append("vitals.on_supplemental_oxygen")
    if data.pregnant and data.scenario != Scenario.MATERNAL:
        # NEWS2 does not apply in pregnancy, so the maternal danger-sign screen is the safety net.
        missing.append("maternal.danger_sign_screen_completed (use scenario 'maternal' for pregnant patients)")
    missing.extend(pack.missing_fields(ctx))
    return missing


def _engine_advisories(data: TriageInput) -> list[Advisory]:
    if data.vitals.spo2_scale == 2:
        return [
            Advisory(
                code="NEWS2_SPO2_SCALE_2",
                message="SpO2 Scale 2 in use: only for patients with a prescribed target of 88-92% (e.g. hypercapnic respiratory failure); confirm the prescription",
                source_id="RCP_NEWS2_2017",
            )
        ]
    return []


def evaluate_triage(data: TriageInput, *, now: datetime | None = None) -> TriageResult:
    ctx = Context.build(data)
    pack = get_pack(data.scenario)

    triggered: list[TriggeredRule] = run_rules(ATP_RULES, ctx)
    triggered += run_rules(pack.urgency_rules, ctx)

    news2_result = _score_news2(data, pack.uses_news2)
    qsofa_result = _score_qsofa(data)
    for score_hit in (_news2_rule(news2_result), _qsofa_rule(qsofa_result)):
        if score_hit is not None:
            triggered.append(score_hit)

    missing = _missing_fields(data, ctx, news2_result, pack)
    below_atp_age = data.age_years is not None and data.age_years < ATP_MIN_AGE
    determination: Determination
    if below_atp_age:
        determination = "outside_validated_population"
    elif missing:
        determination = "insufficient_data"
    else:
        determination = "complete"

    # Completeness gate: GREEN must be earned. Anything else is floored at YELLOW for human review.
    if determination != "complete":
        reason = (
            f"Age {data.age_years} is below the ATP validation population (>= {ATP_MIN_AGE}); GREEN cannot be assigned"
            if below_atp_age
            else "Required data missing; GREEN cannot be assigned without complete data and a completed red-flag screen"
        )
        triggered.append(
            _floor_rule(
                "SAFETY_FLOOR_OUTSIDE_POPULATION" if below_atp_age else "SAFETY_FLOOR_INSUFFICIENT_DATA",
                reason,
                {"missing_fields": ev(missing, "none missing")} if missing else {"age_years": ev(data.age_years, f">= {ATP_MIN_AGE}")},
            )
        )

    urgency = max_urgency(*(t.urgency for t in triggered))
    advisories = _engine_advisories(data) + pack.advisories(ctx)

    return TriageResult(
        urgency=urgency,
        determination=determination,
        needs_human_review=determination != "complete",
        scenario=data.scenario,
        triggered_rules=sorted(triggered, key=lambda t: (-t.urgency.rank, t.rule_id)),
        scores=Scores(news2=news2_result, qsofa=qsofa_result),
        missing_fields=missing,
        advisories=advisories,
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
        evaluated_at=now or datetime.now(timezone.utc),
    )


# ── LLM boundary ─────────────────────────────────────────────────────────


def enforce_raise_only(result: TriageResult, suggested: Urgency | None) -> FinalUrgency:
    """Combine the deterministic result with a model suggestion. The suggestion may raise urgency,
    never lower it. `result` is not modified."""
    deterministic = result.urgency
    final = max_urgency(deterministic, suggested)
    downgrade_refused = suggested is not None and suggested.rank < deterministic.rank
    raised = suggested is not None and suggested.rank > deterministic.rank
    if downgrade_refused:
        reason = "LLM cannot downgrade deterministic safety classification"
    elif raised:
        reason = "Suggestion raised urgency above the deterministic classification"
    else:
        reason = None
    return FinalUrgency(
        deterministic_urgency=deterministic,
        suggested_urgency=suggested,
        final_urgency=final,
        override_applied=downgrade_refused or raised,
        override_reason=reason,
    )
