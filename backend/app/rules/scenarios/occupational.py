"""Occupational health. Hearing threshold shift is a surveillance finding, not triage urgency."""

from app.rules.models import Advisory, Scenario
from app.rules.registry import Context
from app.rules.scenarios import ScenarioPack


def _advisories(ctx: Context) -> list[Advisory]:
    o = ctx.input.occupational
    if o and o.sts_db_avg_2_3_4khz is not None and o.sts_db_avg_2_3_4khz >= 10:
        return [Advisory(code="OCC_STS", message=f"Standard threshold shift {o.sts_db_avg_2_3_4khz} dB (>=10 dB avg at 2/3/4 kHz): audiology review (US OSHA definition)", source_id="OSHA_1910_95")]
    return []


PACK = ScenarioPack(scenario=Scenario.OCCUPATIONAL, uses_news2=True, advisories=_advisories)
