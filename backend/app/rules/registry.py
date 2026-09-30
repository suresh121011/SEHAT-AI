"""Rule representation: small, frozen, individually testable units with a cited source."""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.rules.models import BpReading, EvidenceItem, TriageInput, TriggeredRule
from app.rules.sources import SOURCES, citation
from app.rules.urgency import Urgency

Evidence = dict[str, EvidenceItem]


@dataclass(frozen=True)
class Context:
    """Validated input plus deterministic derived values. Rules read only from here."""

    input: TriageInput
    shock_index: float | None
    bp_pairs: tuple[tuple[int | None, int | None], ...]  # (sbp, dbp) from vitals and any extra readings

    @classmethod
    def build(cls, data: TriageInput) -> "Context":
        v = data.vitals
        # Not rounded: rounding 1.004 down to 1.0 would hide a shock index >1.
        shock_index = v.pulse / v.sbp if v.pulse is not None and v.sbp else None
        readings: list[BpReading] = data.chronic_ncd.bp_readings if data.chronic_ncd else []
        pairs: list[tuple[int | None, int | None]] = [(r.sbp, r.dbp) for r in readings]
        if v.sbp is not None or v.dbp is not None:
            pairs.insert(0, (v.sbp, v.dbp))
        return cls(input=data, shock_index=shock_index, bp_pairs=tuple(pairs))


def ev(value: object, threshold: str) -> EvidenceItem:
    return EvidenceItem(value=value, threshold=threshold)


@dataclass(frozen=True)
class Rule:
    id: str
    family: str
    urgency: Urgency
    reason: str
    source_id: str
    predicate: Callable[[Context], Evidence | None] = field(repr=False)
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.source_id not in SOURCES:
            raise ValueError(f"Rule {self.id} cites unknown source {self.source_id}")

    def evaluate(self, ctx: Context) -> TriggeredRule | None:
        if not self.enabled:
            return None
        evidence = self.predicate(ctx)
        if evidence is None:
            return None
        return hit(self.id, self.family, self.urgency, self.reason, self.source_id, evidence)


def hit(rule_id: str, family: str, urgency: Urgency, reason: str, source_id: str, evidence: Evidence) -> TriggeredRule:
    """Build a triggered-rule record. Used by Rule.evaluate and by the engine's score/floor mappings."""
    if source_id not in SOURCES:
        raise ValueError(f"{rule_id} cites unknown source {source_id}")
    return TriggeredRule(
        rule_id=rule_id,
        family=family,
        urgency=urgency,
        reason=reason,
        source_id=source_id,
        source=citation(source_id),
        evidence=evidence,
    )


def run_rules(rules: tuple[Rule, ...], ctx: Context) -> list[TriggeredRule]:
    return [hit for rule in rules if (hit := rule.evaluate(ctx)) is not None]
