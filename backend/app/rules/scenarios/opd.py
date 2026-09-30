"""OPD triage: ATP + NEWS2 (+ qSOFA when infection is suspected). No extra rules."""

from app.rules.models import Scenario
from app.rules.scenarios import ScenarioPack

PACK = ScenarioPack(scenario=Scenario.OPD, uses_news2=True)
