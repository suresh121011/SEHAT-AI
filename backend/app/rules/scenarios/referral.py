"""Referral handoff. Packet completeness is a process check (advisory), never an urgency input."""

from app.rules.models import Advisory, Scenario
from app.rules.registry import Context
from app.rules.scenarios import ScenarioPack

_CHECKS = ("escort_arranged", "transport_arranged", "identity_confirmed", "consent_taken")


def _advisories(ctx: Context) -> list[Advisory]:
    r = ctx.input.referral
    missing = [c for c in _CHECKS if r is None or getattr(r, c) is not True]
    if missing:
        return [Advisory(code="REF_PACKET_INCOMPLETE", message=f"Referral packet incomplete: {', '.join(missing)}", source_id="SEHAT_POLICY")]
    return []


PACK = ScenarioPack(scenario=Scenario.REFERRAL, uses_news2=True, advisories=_advisories)
