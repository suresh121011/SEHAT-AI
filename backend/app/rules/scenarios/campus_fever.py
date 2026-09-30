"""Campus fever. The WHO ILI case definition is a surveillance label, not an urgency rule.
Fever >39 °C is RED via ATP; qSOFA runs when suspected_infection is set."""

from app.rules.models import Advisory, Scenario
from app.rules.registry import Context
from app.rules.scenarios import ScenarioPack


def _advisories(ctx: Context) -> list[Advisory]:
    c = ctx.input.campus_fever
    t = ctx.input.vitals.temp_c
    if c and c.cough and t is not None and t >= 38.0 and c.onset_days is not None and c.onset_days <= 10:
        return [Advisory(code="CAMPUS_ILI", message="Meets WHO influenza-like illness surveillance definition (fever >=38 C + cough, onset <=10 days)", source_id="WHO_ILI_2014")]
    return []


PACK = ScenarioPack(scenario=Scenario.CAMPUS_FEVER, uses_news2=True, advisories=_advisories)
