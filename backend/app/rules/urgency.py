"""Urgency levels and the single precedence rule: RED > YELLOW > GREEN."""

from enum import Enum


class Urgency(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Urgency.GREEN: 1, Urgency.YELLOW: 2, Urgency.RED: 3}


def max_urgency(*levels: Urgency | None) -> Urgency:
    """Highest urgency among the given levels; GREEN is the floor, never a default for missing data."""
    present = [lvl for lvl in levels if lvl is not None]
    return max(present, key=lambda u: u.rank, default=Urgency.GREEN)
