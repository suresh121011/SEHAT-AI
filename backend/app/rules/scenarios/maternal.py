"""Maternal pack. NEWS2 is not used (RCP: not validated in pregnancy).

Danger signs -> "refer immediately" (MOHFW_MCP); SEHAT maps that to a YELLOW floor. Signs that
ATP classes as RED (active seizures, 3rd-trimester bleeding/pain, BP >220/110, active bleeding)
become RED through the ATP rules.
"""

from app.rules.models import Advisory, Scenario
from app.rules.registry import Context, Evidence, Rule, ev
from app.rules.scenarios import ScenarioPack
from app.rules.urgency import Urgency


def _danger_signs(ctx: Context) -> Evidence | None:
    m = ctx.input.maternal
    if m and m.danger_signs:
        return {"danger_signs": ev(sorted(s.value for s in m.danger_signs), "any MoHFW danger sign -> refer immediately")}
    return None


def _severe_anaemia(ctx: Context) -> Evidence | None:
    m = ctx.input.maternal
    if m and m.hb_g_dl is not None and m.hb_g_dl < 7.0:
        return {"hb_g_dl": ev(m.hb_g_dl, "<7.0 g/dL (<70 g/L) severe anaemia in pregnancy")}
    return None


RULES = (
    Rule("MAT_YELLOW_DANGER_SIGN", "maternal", Urgency.YELLOW, "Pregnancy danger sign present: refer immediately (SEHAT maps to YELLOW minimum)", "MOHFW_MCP", _danger_signs),
    Rule("MAT_YELLOW_SEVERE_ANAEMIA", "maternal", Urgency.YELLOW, "Severe anaemia in pregnancy (Hb <7 g/dL)", "WHO_HB_2024", _severe_anaemia),
)


def _advisories(ctx: Context) -> list[Advisory]:
    age = ctx.input.age_years
    if age is not None and (age < 18 or age > 35):
        return [Advisory(code="MAT_AGE_RISK", message=f"Maternal age {age} (<18 or >35): high-risk pregnancy factor, pending clinical verification", source_id="SEHAT_POLICY")]
    return []


def _missing(ctx: Context) -> list[str]:
    m = ctx.input.maternal
    return [] if m and m.danger_sign_screen_completed else ["maternal.danger_sign_screen_completed"]


PACK = ScenarioPack(scenario=Scenario.MATERNAL, uses_news2=False, urgency_rules=RULES, advisories=_advisories, missing_fields=_missing)
