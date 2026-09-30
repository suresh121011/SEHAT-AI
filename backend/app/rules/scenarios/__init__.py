"""Scenario rule packs. Each pack adds (a) urgency rules that can only raise urgency, (b) advisories
that never affect urgency, and (c) the extra fields needed before GREEN can be earned.
ATP rules apply in every scenario and live in app.rules.atp."""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.rules.models import Advisory, Scenario
from app.rules.registry import Context, Rule


@dataclass(frozen=True)
class ScenarioPack:
    scenario: Scenario
    uses_news2: bool
    urgency_rules: tuple[Rule, ...] = ()
    advisories: Callable[[Context], list[Advisory]] = field(default=lambda ctx: [], repr=False)
    missing_fields: Callable[[Context], list[str]] = field(default=lambda ctx: [], repr=False)


def _registry() -> dict[Scenario, ScenarioPack]:
    from app.rules.scenarios import (
        campus_fever,
        chronic_ncd,
        health_camp,
        maternal,
        occupational,
        opd,
        referral,
    )

    packs = (opd.PACK, maternal.PACK, chronic_ncd.PACK, health_camp.PACK, campus_fever.PACK, occupational.PACK, referral.PACK)
    registry = {p.scenario: p for p in packs}
    if set(registry) != set(Scenario):
        raise RuntimeError("every Scenario needs a rule pack")
    return registry


def get_pack(scenario: Scenario) -> ScenarioPack:
    return PACKS[scenario]


PACKS: dict[Scenario, ScenarioPack] = {}
PACKS.update(_registry())
