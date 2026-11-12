"""Lint the evidence corpus (docs/23; app/evidence/passages.jsonl). Run before every change to the corpus; CI runs it
through tests/evidence/test_evidence.py.

    # from backend/
    <venv>/bin/python scripts/lint_evidence_corpus.py                   # lint the shipped corpus
    <venv>/bin/python scripts/lint_evidence_corpus.py --hash passage.txt  # SHA-256 of a passage's exact text

Checks: every line is a valid passage; ids are unique; every source is registered; every rule exists and cites the
passage's source; the anchors are the rule's threshold numbers; a text has the matching SHA-256 and passes the guard
(instructions, overrides, urgency-lowering, diagnostic or prescriptive wording, identifiers); a hash never stands
without a text; a reviewer and a review date come together and only with a text; a reviewer is on the owner's list
(app/evidence/reviewers.json, which must be valid); every rule the engine can fire has an entry.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def lint(path: Path) -> list[str]:
    from pydantic import ValidationError

    from app.ai import guard
    from app.evidence import gate
    from app.evidence.corpus import Passage, expected_anchors, rule_catalogue
    from app.rules.sources import SOURCES

    catalogue = rule_catalogue()
    problems: list[str] = []
    try:
        allowed = gate.reviewers()
    except (ValueError, OSError) as exc:  # pydantic's ValidationError is a ValueError
        allowed = frozenset()
        problems.append(f"reviewers.json: not a valid reviewer list ({type(exc).__name__})")
    seen: set[str] = set()
    covered: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            p = Passage(**json.loads(line))
        except (ValueError, ValidationError) as exc:
            problems.append(f"line {n}: not a valid passage ({type(exc).__name__})")
            continue
        where = f"line {n} ({p.passage_id})"
        if p.passage_id in seen:
            problems.append(f"{where}: duplicate passage_id")
        seen.add(p.passage_id)
        if p.source_id not in SOURCES:
            problems.append(f"{where}: unknown source {p.source_id}")
        for rule_id in p.rule_ids:
            if rule_id not in catalogue:
                problems.append(f"{where}: unknown rule {rule_id}")
            elif catalogue[rule_id][0] != p.source_id:
                problems.append(f"{where}: rule {rule_id} cites {catalogue[rule_id][0]}, not {p.source_id}")
            elif set(p.anchors) != expected_anchors(rule_id, catalogue[rule_id][1]):
                problems.append(f"{where}: anchors are not the threshold numbers of rule {rule_id}")
            covered.add(rule_id)
        if p.text is None:
            if p.sha256 is not None:
                problems.append(f"{where}: a hash without a text")
        else:
            if p.sha256 != hashlib.sha256(p.text.encode("utf-8")).hexdigest():
                problems.append(f"{where}: sha256 does not match the text")
            if code := guard.check_input(p.text) or guard.check_output(p.text):
                problems.append(f"{where}: the guard blocks the text ({code})")
        if (p.reviewer is None) != (p.reviewed_at is None):
            problems.append(f"{where}: reviewer and reviewed_at come together")
        if p.reviewer is not None and p.text is None:
            problems.append(f"{where}: a reviewer without a text")
        if p.reviewer is not None and p.reviewer not in allowed:
            problems.append(f"{where}: the reviewer is not on the owner's list (app/evidence/reviewers.json)")
    for rule_id in sorted(set(catalogue) - covered):
        problems.append(f"no entry for rule {rule_id}")
    return problems


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if len(argv) == 3 and argv[1] == "--hash":
        text = Path(argv[2]).read_text(encoding="utf-8").rstrip("\n")
        print(hashlib.sha256(text.encode("utf-8")).hexdigest())
        return 0
    from app.evidence.corpus import CORPUS_PATH

    problems = lint(CORPUS_PATH)
    for p in problems:
        print(f"EVIDENCE CORPUS: {p}")
    print("evidence corpus: ok" if not problems else f"evidence corpus: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
