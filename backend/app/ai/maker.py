"""MAKER-style multi-pass voting (docs/16 §5; after arXiv:2511.09030, basic form).

The same extraction is requested N times (default 3, temperatures 0.1/0.2/0.3). Each reply is
schema-validated and grounded first; an invalid reply abstains. Voting is pure (`vote`) and
asymmetric (council amendment):

- values (complaint, measurements, symptoms, medications): unanimous among valid passes → `agreed`;
  a strict majority → `majority`, except critical vitals/labs → `disputed` with value None and the
  candidates listed for human entry; anything else → `disputed`;
- alarms (red-flag mentions): never voted away. A grounded, non-negated mention from even one pass
  surfaces — `agreed` if every valid pass has it, otherwise `disputed_raise` for human review;
- the urgency suggestion passes to `enforce_raise_only` only when every valid pass gives the same
  level; otherwise it is withheld and the candidate levels are reported for human review;
- fewer than 2 valid passes → `insufficient_agreement`: no fields are produced.

Agreement is reported as a count ("3/3"), not a probability: passes from one model share its errors,
and a deterministic provider always agrees with itself.
"""

from collections import Counter
from dataclasses import dataclass, field

import anyio
from pydantic import ValidationError

from app.ai.grounding import Entry, Grounded, ground
from app.ai.schemas import ExtractionOutput

TEMPERATURES = (0.1, 0.2, 0.3, 0.4, 0.5)
MIN_VALID_PASSES = 2
NOT_MENTIONED = "__not_mentioned__"


@dataclass
class VotedField:
    key: str
    kind: str
    status: str  # agreed | majority | disputed | disputed_raise
    agreement: str  # "k/n": passes supporting the shown value (or the alarm) out of valid passes
    value: object | None  # None when disputed: a human must enter it
    candidates: list[dict]  # [{value, passes}] — always listed when not unanimous
    evidence: list[dict]
    critical: bool
    priority_review: bool
    flags: list[str]
    origin: str = "model"  # model (provider passes, voted) | keyword_rule (app.ai.redflag_keywords, not a model)


@dataclass
class VoteResult:
    status: str  # completed | insufficient_agreement
    passes_requested: int
    passes_valid: int
    fields: list[VotedField] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    urgency_suggestion: str | None = None  # only when unanimous
    urgency_candidates: list[dict] = field(default_factory=list)
    urgency_evidence: list[dict] = field(default_factory=list)  # quotes behind every candidate level
    abstentions: list[dict] = field(default_factory=list)


def _ev(entries: list[Entry]) -> list[dict]:
    seen, out = set(), []
    for e in entries:
        for ev in e.evidence:
            k = (ev.segment_id, ev.quote)
            if k not in seen:
                seen.add(k)
                out.append({"segment_id": ev.segment_id, "quote": ev.quote})
    return out


def vote(passes: list[Grounded | None], passes_requested: int | None = None, abstentions: list[dict] | None = None) -> VoteResult:
    requested = passes_requested if passes_requested is not None else len(passes)
    valid = [p for p in passes if p is not None]
    n = len(valid)
    res = VoteResult("completed", requested, n, abstentions=list(abstentions or []))
    for p in valid:
        for d in p.dropped:
            if d not in res.dropped:
                res.dropped.append(d)
    if n < MIN_VALID_PASSES:
        res.status = "insufficient_agreement"
        return res

    keys: list[str] = []
    by_pass: list[dict[str, Entry]] = []
    for p in valid:
        m: dict[str, Entry] = {}
        for e in p.entries:
            m.setdefault(e.key, e)
            if e.key not in keys:
                keys.append(e.key)
        by_pass.append(m)

    for key in keys:
        present = [m[key] for m in by_pass if key in m]
        sample = present[0]
        flags = sorted({f for e in present for f in e.flags})
        if sample.kind == "urgency":
            counts = Counter(e.vote for e in present)
            res.urgency_candidates = [{"level": lvl, "passes": c} for lvl, c in counts.most_common()]
            res.urgency_evidence = _ev(present)
            if len(present) == n and len(counts) == 1:
                res.urgency_suggestion = sample.vote
            continue

        votes = [m[key].vote if key in m else NOT_MENTIONED for m in by_pass]
        counts = Counter(votes)
        candidates = [
            {"value": next(e.value for e in present if e.vote == v) if v != NOT_MENTIONED else None, "passes": c, **({"not_mentioned": True} if v == NOT_MENTIONED else {})}
            for v, c in counts.most_common()
        ]

        if sample.kind == "red_flag":
            alarm = [e for e in present if e.vote is False]  # not negated
            if alarm:
                unanimous = len(alarm) == n
                res.fields.append(VotedField(key, "red_flag", "agreed" if unanimous else "disputed_raise", f"{len(alarm)}/{n}",
                                             alarm[0].value, [] if unanimous else candidates, _ev(alarm), True, True, flags))
            else:  # every mention negated (with a cue in the quote): reported, never an alarm
                res.fields.append(VotedField(key, "red_flag", "agreed" if len(present) == n else "majority" if 2 * len(present) > n else "disputed",
                                             f"{len(present)}/{n}", sample.value, [] if len(present) == n else candidates, _ev(present), True, False, flags))
            continue

        top, top_count = counts.most_common(1)[0]
        winners = [e for e in present if e.vote == top]
        if top_count == n and top != NOT_MENTIONED:
            status, value, ev = "agreed", winners[0].value, _ev(winners)
        elif top != NOT_MENTIONED and 2 * top_count > n and not sample.critical:
            status, value, ev = "majority", winners[0].value, _ev(winners)
        else:
            status, value, ev = "disputed", None, _ev(present)
        res.fields.append(VotedField(key, sample.kind, status, f"{top_count if top != NOT_MENTIONED else len(present)}/{n}", value,
                                     [] if status == "agreed" else candidates, ev, sample.critical,
                                     sample.critical and status != "agreed" or bool(flags), flags))
    return res


async def run_passes(provider, prompt, passes: int) -> VoteResult:
    """Run `passes` provider calls concurrently, validate and ground each, then vote. A pass that raises,
    returns invalid JSON or fails the schema abstains (reason code only). Raises RuntimeError only if every
    pass raised (the provider is unavailable) so the caller can report an adapter error."""
    segments = prompt.segment_texts()
    results: list[Grounded | None] = [None] * passes
    abstentions: list[dict] = []
    errors = 0

    async def one(i: int) -> None:
        nonlocal errors
        try:
            reply = await provider.generate(prompt, temperature=TEMPERATURES[i], pass_index=i)
        except Exception:
            errors += 1
            abstentions.append({"pass": i, "reason": "provider_error"})
            return
        try:
            out = ExtractionOutput.model_validate_json(reply.raw_json)
        except ValidationError:
            abstentions.append({"pass": i, "reason": "schema_invalid"})
            return
        results[i] = ground(segments, out)

    async with anyio.create_task_group() as tg:
        for i in range(passes):
            tg.start_soon(one, i)
    if errors == passes:
        raise RuntimeError("all provider passes failed")
    abstentions.sort(key=lambda a: a["pass"])
    return vote(results, passes, abstentions)
