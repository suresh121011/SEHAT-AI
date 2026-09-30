"""qSOFA (SEPSIS3_2016): RR ≥22, altered mentation, SBP ≤100; ≥2 is positive.

A bedside prompt for adults with suspected infection. Not a stand-alone definition of sepsis.
"""

from app.rules.models import QsofaResult, ScoreComponent, Vitals


def _altered_mentation(v: Vitals) -> bool | None:
    signals = []
    if v.consciousness is not None:
        signals.append(v.consciousness != "A")
    if v.gcs is not None:
        signals.append(v.gcs <= 13)  # Sepsis-3 derivation threshold
    return any(signals) if signals else None


def score_qsofa(v: Vitals) -> QsofaResult:
    rr = None if v.resp_rate is None else int(v.resp_rate >= 22)
    sbp = None if v.sbp is None else int(v.sbp <= 100)
    mentation = _altered_mentation(v)
    components = {
        "resp_rate": ScoreComponent(value=v.resp_rate, points=rr),
        "sbp": ScoreComponent(value=v.sbp, points=sbp),
        "altered_mentation": ScoreComponent(value={"consciousness": v.consciousness, "gcs": v.gcs}, points=None if mentation is None else int(mentation)),
    }
    points = [c.points for c in components.values() if c.points is not None]
    total = sum(points)
    if len(points) == 3:
        return QsofaResult(status="complete", total=total, positive=total >= 2, components=components)
    # Incomplete: positive can still be established from available criteria; negative cannot.
    return QsofaResult(
        status="incomplete",
        reason="missing criteria; a negative result cannot be concluded",
        total=total,
        positive=True if total >= 2 else None,
        components=components,
    )


def not_applicable(reason: str) -> QsofaResult:
    return QsofaResult(status="not_applicable", reason=reason)
