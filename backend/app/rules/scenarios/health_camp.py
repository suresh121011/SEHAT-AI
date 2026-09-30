"""Health camp screening. CBAC is risk stratification for NCD screening, not triage urgency."""

from app.rules.models import Advisory, Scenario
from app.rules.registry import Context
from app.rules.scenarios import ScenarioPack


def _advisories(ctx: Context) -> list[Advisory]:
    h = ctx.input.health_camp
    if h and h.cbac_score is not None and h.cbac_score > 4:
        return [Advisory(code="CAMP_CBAC_HIGH", message=f"CBAC score {h.cbac_score} (>4): prioritise for NCD screening", source_id="NPCDCS_CBAC")]
    return []


PACK = ScenarioPack(scenario=Scenario.HEALTH_CAMP, uses_news2=True, advisories=_advisories)
