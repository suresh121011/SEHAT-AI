"""Chronic NCD follow-up. IHCI: BP >180/110 -> assess target organ damage, refer immediately (YELLOW).
BP >220/110 is RED via ATP."""

from app.rules.models import Scenario
from app.rules.registry import Context, Evidence, Rule, ev
from app.rules.scenarios import ScenarioPack
from app.rules.urgency import Urgency

MIN_READINGS = 2  # arch §11: "Two BP readings"


def _severe_bp(ctx: Context) -> Evidence | None:
    for sbp, dbp in ctx.bp_pairs:
        if (sbp is not None and sbp > 180) or (dbp is not None and dbp > 110):
            return {"bp": ev(f"{sbp}/{dbp}", "SBP >180 or DBP >110 mmHg")}
    return None


RULES = (
    Rule("NCD_YELLOW_BP_OVER_180_110", "chronic_ncd", Urgency.YELLOW, "BP >180/110: assess for acute target organ damage and refer immediately", "IHCI_HTN", _severe_bp),
)


def _missing(ctx: Context) -> list[str]:
    n = ctx.input.chronic_ncd
    count = len(n.bp_readings) if n else 0
    return [] if count >= MIN_READINGS else [f"chronic_ncd.bp_readings (need {MIN_READINGS}, have {count})"]


PACK = ScenarioPack(scenario=Scenario.CHRONIC_NCD, uses_news2=True, urgency_rules=RULES, missing_fields=_missing)
