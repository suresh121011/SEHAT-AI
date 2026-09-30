"""AIIMS Triage Protocol RED criteria (ATP_2022, Supplementary Table 1). Applied in every scenario.

ATP defines RED explicitly; YELLOW/GREEN are qualitative (ATP_2020). "Triage officer discretion"
is not computable and is handled by the human reviewer override, not by a rule here.
"""

from app.rules.models import AtpFlag, MaternalDangerSign
from app.rules.registry import Context, Evidence, Rule, ev
from app.rules.urgency import Urgency

# Fever cut-off for "pulse >120 (without fever)": ATP does not define fever numerically.
# SEHAT_POLICY uses the NEWS2 normal upper bound (38.0 °C); unknown temperature counts as afebrile
# so that the tachycardia rule still fires.
FEVER_ABOVE_C = 38.0


def _rr(ctx: Context) -> Evidence | None:
    rr = ctx.input.vitals.resp_rate
    if rr is not None and (rr > 22 or rr < 10):
        return {"resp_rate": ev(rr, ">22 or <10 /min")}
    return None


def _spo2(ctx: Context) -> Evidence | None:
    spo2 = ctx.input.vitals.spo2
    if spo2 is not None and spo2 < 90:
        return {"spo2": ev(spo2, "<90 %")}
    return None


def _pulse(ctx: Context) -> Evidence | None:
    v = ctx.input.vitals
    if v.pulse is None:
        return None
    if v.pulse < 50:
        return {"pulse": ev(v.pulse, "<50 /min")}
    afebrile = v.temp_c is None or v.temp_c <= FEVER_ABOVE_C
    if v.pulse > 120 and afebrile:
        evidence = {"pulse": ev(v.pulse, ">120 /min without fever")}
        evidence["temp_c"] = ev(v.temp_c, f"<={FEVER_ABOVE_C} °C or unknown (SEHAT_POLICY)")
        return evidence
    return None


def _bp_high(ctx: Context) -> Evidence | None:
    for sbp, dbp in ctx.bp_pairs:
        if (sbp is not None and sbp > 220) or (dbp is not None and dbp > 110):
            return {"bp": ev(f"{sbp}/{dbp}", "SBP >220 or DBP >110 mmHg")}
    return None


def _bp_low(ctx: Context) -> Evidence | None:
    for sbp, dbp in ctx.bp_pairs:
        if (sbp is not None and sbp < 90) or (dbp is not None and dbp < 60):
            return {"bp": ev(f"{sbp}/{dbp}", "SBP <90 or DBP <60 mmHg")}
    return None


def _shock_index(ctx: Context) -> Evidence | None:
    if ctx.shock_index is not None and ctx.shock_index > 1:
        v = ctx.input.vitals
        return {"shock_index": ev(round(ctx.shock_index, 3), ">1 (pulse/SBP)"), "pulse": ev(v.pulse, "-"), "sbp": ev(v.sbp, "-")}
    return None


def _sensorium(ctx: Context) -> Evidence | None:
    c = ctx.input.vitals.consciousness
    if c in ("V", "P", "U"):
        return {"consciousness": ev(c, "responds only to Voice/Pain or Unresponsive")}
    return None


def _fever_over_39(ctx: Context) -> Evidence | None:
    t = ctx.input.vitals.temp_c
    if t is not None and t > 39.0:
        return {"temp_c": ev(t, ">39 °C")}
    return None


def _flag(flag: AtpFlag):
    def predicate(ctx: Context) -> Evidence | None:
        if flag in ctx.input.red_flags_present:
            return {"red_flag": ev(flag.value, "present on assessment")}
        return None

    return predicate


def _third_trimester_bleeding_from_danger_signs(ctx: Context) -> Evidence | None:
    """Derives the ATP row from maternal data so a 3rd-trimester bleed is RED even if only the danger sign was ticked."""
    m = ctx.input.maternal
    if ctx.input.trimester == 3 and m is not None and MaternalDangerSign.VAGINAL_BLEEDING in m.danger_signs:
        return {"trimester": ev(3, "3rd trimester"), "danger_sign": ev("vaginal_bleeding", "present")}
    return None


def _r(rule_id: str, reason: str, predicate) -> Rule:
    return Rule(rule_id, "atp", Urgency.RED, reason, "ATP_2022", predicate)


_VITAL_RULES = (
    _r("ATP_RED_RR", "Breathing compromise: respiratory rate >22 or <10", _rr),
    _r("ATP_RED_SPO2", "Breathing compromise: SpO2 <90%", _spo2),
    _r("ATP_RED_PULSE", "Circulation compromise: pulse <50 or >120 without fever", _pulse),
    _r("ATP_RED_BP_HIGH", "Circulation compromise: SBP >220 or DBP >110", _bp_high),
    _r("ATP_RED_BP_LOW", "Circulation compromise: SBP <90 or DBP <60", _bp_low),
    _r("ATP_RED_SHOCK_INDEX", "Circulation compromise: shock index >1", _shock_index),
    _r("ATP_RED_SENSORIUM", "Disability: altered sensorium (V/P/U)", _sensorium),
    _r("ATP_RED_FEVER_39", "Time-sensitive: fever with temperature >39 °C", _fever_over_39),
    _r("ATP_RED_THIRD_TRIMESTER_BLEEDING_DERIVED", "Pregnancy in 3rd trimester with vaginal bleeding", _third_trimester_bleeding_from_danger_signs),
)

_FLAG_REASONS: dict[AtpFlag, str] = {
    AtpFlag.STRIDOR: "Airway compromise: stridor/noisy breathing",
    AtpFlag.ANGIOEDEMA_FACE: "Airway compromise: angioedema involving the face",
    AtpFlag.ACTIVE_SEIZURE: "Airway compromise: active seizures",
    AtpFlag.INCOMPLETE_SENTENCES: "Breathing compromise: talking in incomplete sentences",
    AtpFlag.AUDIBLE_WHEEZE: "Breathing compromise: audible wheeze",
    AtpFlag.ACTIVE_BLEEDING: "Circulation compromise: active bleeding",
    AtpFlag.CHEST_PAIN_ACUTE_24H: "Time-sensitive: acute chest pain (<24 h)",
    AtpFlag.LIMB_WEAKNESS_24H: "Time-sensitive: limb weakness <24 h",
    AtpFlag.STROKE_SUSPECTED_24H: "Time-sensitive: suspected stroke within 24 h of onset",
    AtpFlag.DANGEROUS_MECHANISM_TRAUMA: "Time-sensitive: drowning/hanging/electrocution/dangerous-mechanism trauma",
    AtpFlag.SOB_ACUTE_12H: "Time-sensitive: acute onset shortness of breath within 12 h",
    AtpFlag.LIMB_ISCHAEMIA_48H: "Time-sensitive: acute limb ischaemia <48 h",
    AtpFlag.ALLERGIC_REACTION: "Time-sensitive: allergic reaction",
    AtpFlag.SCROTAL_PAIN_YOUNG_MALE: "Time-sensitive: acute scrotal/inguinal pain in young male",
    AtpFlag.SEVERE_PAIN: "Time-sensitive: severe pain anywhere in body",
    AtpFlag.SUDDEN_ABDOMINAL_PAIN: "Time-sensitive: sudden onset abdominal pain",
    AtpFlag.SUDDEN_HEADACHE: "Time-sensitive: sudden headache",
    AtpFlag.URINARY_RETENTION: "Time-sensitive: acute urinary retention",
    AtpFlag.FEVER_IMMUNOCOMPROMISED: "Time-sensitive: fever with aplastic anaemia, acute leukaemia or chemotherapy within 14 days",
    AtpFlag.OUTSIDE_EVAL_TIME_SENSITIVE: "Time-sensitive: outside evaluation suggests ACS, aortic dissection, stroke, sepsis or K+ >5.5",
    AtpFlag.SYNCOPE: "Time-sensitive: history of syncope",
    AtpFlag.NEEDLE_PRICK_INJURY: "Time-sensitive: needle prick injury",
    AtpFlag.ABD_PAIN_WITH_VAGINAL_BLEEDING: "Increased urgency: abdominal pain with vaginal bleeding",
    AtpFlag.AGITATED_VIOLENT: "Increased urgency: agitated or violent patient",
    AtpFlag.POISONING_ENVENOMATION: "Increased urgency: suspected poisoning/snake bite/scorpion sting",
    AtpFlag.THIRD_TRIMESTER_PAIN_OR_BLEEDING: "Increased urgency: pregnancy in 3rd trimester with abdominal pain/vaginal bleeding",
}

if set(_FLAG_REASONS) != set(AtpFlag):
    raise RuntimeError("every AtpFlag needs an ATP rule")

_FLAG_RULES = tuple(_r(f"ATP_RED_{flag.name}", reason, _flag(flag)) for flag, reason in _FLAG_REASONS.items())

ATP_RULES: tuple[Rule, ...] = _VITAL_RULES + _FLAG_RULES

# Inputs the ATP vital rules need before GREEN can be earned.
ATP_REQUIRED_VITALS = ("resp_rate", "spo2", "pulse", "sbp", "dbp", "temp_c", "consciousness")
