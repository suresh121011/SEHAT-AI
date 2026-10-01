"""RxNorm cross-check for drug names on prescriptions — architecture §10 Gödel step 2 (fuzzy match ≥ 0.8;
similarity < 0.95 → UncertainDrug; no match → UnknownDrug). Fully offline.

Data: NLM "RxNorm Current Prescribable Content" (no licence required — nlm.nih.gov, read 2026-10-01),
monthly release, loaded by scripts/build_rxnorm_index.py into a small SQLite index. No drug name, or
anything else, is sent anywhere.

Safety rules (council R3.6):
- "matched" only for an exact normalized match to an RxNorm ingredient (TTY IN/PIN/MIN); a printed
  strength must also appear in an RxNorm clinical drug (SCD) for that ingredient, else "uncertain".
- Brand names (TTY BN, US brands) and fuzzy matches are at most "uncertain" and list candidates.
- RxNorm is a US terminology: Indian brand names will often be "unknown". That is reported honestly and
  routes to the reviewer; it is never "corrected" to the closest name.
- WHO INN names that differ from US names (e.g. paracetamol/acetaminophen) are matched through a small
  listed synonym table and are at most "uncertain".
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

MATCH_THRESHOLD = 0.8  # architecture §10
CERTAIN_THRESHOLD = 0.95  # architecture §10

# Names common on Indian prescriptions (WHO INN or older British Approved Names) → the US name RxNorm uses.
# Any match through this table is at most "uncertain": the reviewer confirms it.
INN_TO_USAN: dict[str, str] = {
    "paracetamol": "acetaminophen", "salbutamol": "albuterol", "glibenclamide": "glyburide", "pethidine": "meperidine",
    "rifampicin": "rifampin", "isoprenaline": "isoproterenol",  # INN ≠ USAN
    "adrenaline": "epinephrine", "noradrenaline": "norepinephrine", "frusemide": "furosemide", "lignocaine": "lidocaine",
    "amoxycillin": "amoxicillin",  # older British Approved Names
}

Status = Literal["matched", "uncertain", "unknown"]


@dataclass(frozen=True)
class RxMatch:
    printed: str
    status: Status
    rxcui: str | None = None
    name: str | None = None
    tty: str | None = None
    similarity: float = 0.0
    strength_match: bool | None = None
    candidates: tuple[tuple[str, str, float], ...] = field(default=())  # (rxcui, name, similarity)
    reason: str = ""


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class RxIndex:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        rows = self.conn.execute("SELECT norm, rxcui, str, tty FROM names").fetchall()
        self.by_norm: dict[str, list[tuple[str, str, str]]] = {}
        for n, cui, s, tty in rows:
            self.by_norm.setdefault(n, []).append((cui, s, tty))
        self.keys = list(self.by_norm)
        self.release = (self.conn.execute("SELECT value FROM meta WHERE key='release'").fetchone() or ("unknown",))[0]

    def _strength_ok(self, ingredient: str, strength: str) -> bool:
        num = re.match(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|%)", strength.lower())
        if not num:
            return False
        pat = f"%{ingredient}%{num.group(1)} {num.group(2).upper() if num.group(2) != 'ml' else 'ML'}%"
        return self.conn.execute("SELECT 1 FROM names WHERE tty='SCD' AND upper(str) LIKE upper(?) LIMIT 1", (pat,)).fetchone() is not None

    def match(self, printed_name: str, strength: str | None = None) -> RxMatch:
        key = norm(printed_name)
        if not key:
            return RxMatch(printed_name, "unknown", reason="empty")
        via_inn = INN_TO_USAN.get(key)
        lookup = via_inn or key
        exact = self.by_norm.get(lookup, [])
        ingredients = [e for e in exact if e[2] in ("IN", "PIN", "MIN")]
        if ingredients:
            cui, name, tty = ingredients[0]
            s_ok = self._strength_ok(name, strength) if strength else None
            status: Status = "matched" if (not via_inn and s_ok is not False) else "uncertain"
            reason = "inn_synonym" if via_inn else ("strength_not_found" if s_ok is False else "exact_ingredient")
            return RxMatch(printed_name, status, cui, name, tty, 1.0, s_ok, reason=reason)
        brands = [e for e in exact if e[2] == "BN"]
        if brands:
            cui, name, tty = brands[0]
            return RxMatch(printed_name, "uncertain", cui, name, tty, 1.0, None, reason="brand_name_us")
        close = difflib.get_close_matches(lookup, self.keys, n=3, cutoff=MATCH_THRESHOLD)
        if not close:
            return RxMatch(printed_name, "unknown", reason="no_match_at_0.8")
        cands = []
        for c in close:
            cui, name, tty = self.by_norm[c][0]
            cands.append((cui, name, round(difflib.SequenceMatcher(None, lookup, c).ratio(), 3)))
        best = cands[0]
        # fuzzy matches are never "matched", even ≥ 0.95: a one-letter difference can be a different drug
        return RxMatch(printed_name, "uncertain", best[0], best[1], None, best[2], None, tuple(cands),
                       reason="fuzzy_below_0.95" if best[2] < CERTAIN_THRESHOLD else "fuzzy_not_exact")


@lru_cache(maxsize=2)
def load_index(db_path: str) -> RxIndex | None:
    p = Path(db_path)
    return RxIndex(p) if p.is_file() else None
