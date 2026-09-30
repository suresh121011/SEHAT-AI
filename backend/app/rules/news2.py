"""NEWS2 (RCP_NEWS2_2017). Scoring (Chart 1) is kept separate from interpretation (Chart 2)
and from the SEHAT urgency mapping (engine).

RCP: NEWS2 is not for use in children (<16 years) or pregnancy.
"""

from typing import Literal

from app.rules.models import News2Result, ScoreComponent, Vitals

PARAMETERS = ("resp_rate", "spo2", "on_supplemental_oxygen", "sbp", "pulse", "consciousness", "temp_c")

Band = Literal["low", "low_medium", "medium", "high"]


def resp_rate_points(rr: int) -> int:
    if rr <= 8:
        return 3
    if rr <= 11:
        return 1
    if rr <= 20:
        return 0
    if rr <= 24:
        return 2
    return 3


def spo2_scale1_points(spo2: int) -> int:
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def spo2_scale2_points(spo2: int, on_oxygen: bool) -> int:
    if spo2 <= 83:
        return 3
    if spo2 <= 85:
        return 2
    if spo2 <= 87:
        return 1
    if spo2 <= 92 or not on_oxygen:  # 88–92, or ≥93 on air
        return 0
    if spo2 <= 94:
        return 1
    if spo2 <= 96:
        return 2
    return 3


def oxygen_points(on_oxygen: bool) -> int:
    return 2 if on_oxygen else 0


def sbp_points(sbp: int) -> int:
    if sbp <= 90:
        return 3
    if sbp <= 100:
        return 2
    if sbp <= 110:
        return 1
    if sbp <= 219:
        return 0
    return 3


def pulse_points(pulse: int) -> int:
    if pulse <= 40:
        return 3
    if pulse <= 50:
        return 1
    if pulse <= 90:
        return 0
    if pulse <= 110:
        return 1
    if pulse <= 130:
        return 2
    return 3


def consciousness_points(acvpu: str) -> int:
    return 0 if acvpu == "A" else 3


def temp_points(temp_c: float) -> int:
    if temp_c <= 35.0:
        return 3
    if temp_c <= 36.0:
        return 1
    if temp_c <= 38.0:
        return 0
    if temp_c <= 39.0:
        return 1
    return 2


def band(total: int, any_single_three: bool) -> Band:
    """RCP Chart 2 clinical risk."""
    if total >= 7:
        return "high"
    if total >= 5:
        return "medium"
    if any_single_three:
        return "low_medium"
    return "low"


def score_news2(v: Vitals) -> News2Result:
    """Score every available parameter. Missing parameters are never assumed normal:
    the result is 'incomplete' and only a lower-bound partial total is reported."""
    components: dict[str, ScoreComponent] = {}

    def add(name: str, value, points: int | None) -> None:
        components[name] = ScoreComponent(value=value, points=points)

    add("resp_rate", v.resp_rate, None if v.resp_rate is None else resp_rate_points(v.resp_rate))
    spo2_value = {"spo2": v.spo2, "scale": v.spo2_scale}  # the scale changes the points, so it is always shown
    if v.spo2 is None:
        add("spo2", spo2_value, None)
    elif v.spo2_scale == 2:
        if v.on_supplemental_oxygen is None and v.spo2 >= 93:
            add("spo2", spo2_value, None)  # Scale 2 points for ≥93 depend on air vs oxygen
        else:
            add("spo2", spo2_value, spo2_scale2_points(v.spo2, bool(v.on_supplemental_oxygen)))
    else:
        add("spo2", spo2_value, spo2_scale1_points(v.spo2))
    add(
        "on_supplemental_oxygen",
        v.on_supplemental_oxygen,
        None if v.on_supplemental_oxygen is None else oxygen_points(v.on_supplemental_oxygen),
    )
    add("sbp", v.sbp, None if v.sbp is None else sbp_points(v.sbp))
    add("pulse", v.pulse, None if v.pulse is None else pulse_points(v.pulse))
    add("consciousness", v.consciousness, None if v.consciousness is None else consciousness_points(v.consciousness))
    add("temp_c", v.temp_c, None if v.temp_c is None else temp_points(v.temp_c))

    scored = [c.points for c in components.values() if c.points is not None]
    total = sum(scored)
    any_three = any(p == 3 for p in scored)
    if len(scored) == len(PARAMETERS):
        return News2Result(status="complete", total=total, band=band(total, any_three), components=components)
    missing = [k for k, c in components.items() if c.points is None]
    return News2Result(
        status="incomplete",
        reason=f"missing: {', '.join(missing)}",
        partial_total=total,
        band=band(total, any_three),  # lower bound: true band can only be the same or higher
        components=components,
    )


def not_applicable(reason: str) -> News2Result:
    return News2Result(status="not_applicable", reason=reason)
