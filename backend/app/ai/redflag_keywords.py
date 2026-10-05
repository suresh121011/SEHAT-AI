"""Deterministic red-flag keyword suggester (docs/16 §2c). NOT an AI model and NOT a rule result.

Why: the local general-purpose model surfaced 0/15 expected red flags on the smoke set; listing the ATP flags in its
prompt raised that to 5/15 but cut grounding from 77 % to 52 % and pushed latency past the gate (docs/16 §2a.4). So the
model prompt stays focused on symptoms, vitals and medicines, and this module matches a short, curated list of
phrases against the same redacted segments the model saw, mapping each to an existing `AtpFlag`.

What a hit is, and is not:
- a CANDIDATE for the health worker's red-flag screen, stored as its own field with origin `keyword_rule` and
  provenance `rules_keyword` (not AI model output), needing a per-field human decision like every AI field;
- never a ticked flag: the rules engine acts only on flags a human enters, and its mandatory red-flag screen is asked
  whatever this module finds. A phrase the list does not know is simply missed — this is an assist, not a safety net;
- not clinician-validated. Time windows in flag names (chest pain "within 24 h", breathlessness "within 12 h") are not
  checked; such hits carry `time_window_not_checked`.

Negation is handled like grounding (app.ai.grounding.scoped_negation): a hit whose clause opens with a negation cue
("No chest pain", "Patient denies fainting") is not suggested. A cue elsewhere in the clause keeps the hit and flags
`negation_conflict`; a past-time cue ("5 years ago", "history of") keeps it and flags `past_history_cue`. Both can only
err toward an extra review, never toward hiding an alarm. Pure: no I/O.
"""

import re
from dataclasses import dataclass

from app.ai.grounding import _NEGATION_RX, norm
from app.rules.models import AtpFlag

RULES_VERSION = "redflag-keywords-2026-10-05.1"
PROVIDER = "rules_keyword"
PROVENANCE = {"source_type": "keyword_rule", "provider": PROVIDER, "rules_version": RULES_VERSION, "ai_model_output": False,
              "label": "Keyword rule — not AI model output"}

_I = re.IGNORECASE
_TIME = "time_window_not_checked"
_BODY = r"(?:abdominal|abdomen|stomach|belly|tummy)"

# (flag, pattern, extra review flags). Phrases are deliberately specific; a broader phrase belongs here only if a
# false suggestion costs a human one click and a miss could hide an alarm.
RULES: list[tuple[AtpFlag, str, tuple[str, ...]]] = [
    (AtpFlag.POISONING_ENVENOMATION, r"snake ?bites?|(?:bitten|bit) by (?:a )?snake|by a snake|scorpion sting|stung by (?:a )?scorpion", ()),
    (AtpFlag.POISONING_ENVENOMATION, r"(?:drank|drunk|consumed|ingested|swallowed|took|taken)\s+(?:some\s+)?(?:\w+\s+)?(?:poison|pesticide|insecticide|"
                                     r"rat poison|kerosene|phenyl|organophosphate|acid)|poisoning|overdose", ()),
    (AtpFlag.NEEDLE_PRICK_INJURY, r"needle ?(?:prick|stick)|needlestick|pricked by (?:a )?(?:used )?(?:needle|syringe)", ()),
    (AtpFlag.URINARY_RETENTION, r"(?:cannot|can't|can not|unable to|not able to|has not|hasn't|have not|haven't)\s+(?:been able to\s+)?pass(?:ed)? urine|"
                                r"urinary retention|retention of urine", ()),
    (AtpFlag.SUDDEN_HEADACHE, r"worst headache|thunderclap headache|sudden(?:ly)?(?: onset)?(?: severe)? headache|"
                              r"headache (?:that )?(?:came on|started|began) (?:all of a )?sudden(?:ly)?|"
                              r"headache\b[^.;]{0,25}\b(?:came on|started|began) (?:all of a )?sudden(?:ly)?", ()),
    (AtpFlag.SUDDEN_ABDOMINAL_PAIN, rf"sudden(?:ly)?(?: onset)?(?: severe)? {_BODY} pain|sudden(?: severe)? pain in (?:the )?{_BODY}", ()),
    (AtpFlag.SEVERE_PAIN, r"severe (?:\w+ )?pain|severe (?:\w+ )?ache|(?:pain|pain score|pain severity)\s*(?:is|of|:)?\s*(?:8|9|10)\s*(?:/|out of)\s*10|"
                          r"severe headache", ()),
    (AtpFlag.SYNCOPE, r"fainted|fainting|passed out|lost consciousness|loss of consciousness|syncope|(?:was|became|went) unconscious", ()),
    (AtpFlag.ACTIVE_SEIZURE, r"seizures?|convulsions?|convulsing|fitting|having fits", ()),
    (AtpFlag.CHEST_PAIN_ACUTE_24H, r"chest pain|pain in (?:the )?chest|crushing (?:\w+ )?(?:pain|pressure)|chest tightness|tightness in (?:the )?chest", (_TIME,)),
    (AtpFlag.LIMB_WEAKNESS_24H, r"weakness (?:of|in) (?:the )?(?:right|left)? ?(?:arm|leg|hand|limb|side)s?|(?:right|left)[- ]sided weakness|"
                                r"(?:right|left)? ?(?:arm|leg)s? (?:is |are |became |went )?(?:weak|numb)\b", (_TIME,)),
    (AtpFlag.STROKE_SUSPECTED_24H, r"slurred speech|speech (?:is |was |became )?slurred|(?:face|facial|mouth)\s+(?:is\s+)?(?:droop\w*|deviat\w*)|"
                                   r"(?:face|mouth) (?:is )?(?:drooping|deviated)|side of (?:the )?face (?:is )?droop\w*", (_TIME,)),
    (AtpFlag.SOB_ACUTE_12H, r"sudden(?:ly)? (?:short of breath|shortness of breath|breathless\w*)|"
                            r"(?:breathless\w*|short(?:ness)? of breath|difficulty (?:in )?breathing) (?:since|for) (?:\d+|one|two|three|four|five|six) hours?", (_TIME,)),
    (AtpFlag.INCOMPLETE_SENTENCES, r"(?:cannot|can't|unable to|not able to) (?:complete|finish|speak in (?:full|complete)) sentences|"
                                   r"(?:speak|speaking|talk|talking) (?:only )?in (?:single |one |short )?words|can only speak in (?:single |one |short )?words", ()),
    (AtpFlag.ACTIVE_BLEEDING, r"heavy bleeding|bleeding heavily|profuse(?:ly)? bleeding|bleeding profusely|active bleeding|vomiting blood|"
                              r"blood in (?:the )?vomit|ha?ematemesis|coughing (?:up )?blood|bleeding (?:is )?not stopping", ()),
    (AtpFlag.ANGIOEDEMA_FACE, r"(?:lips?|face|tongue|eyelids?)(?: and (?:lips?|face|tongue|eyelids?))? (?:is |are )?(?:swelling|swollen)|"
                              r"swelling of (?:the )?(?:lips?|face|tongue|eyelids?)|swollen (?:lips?|face|tongue|eyelids?)", ()),
    (AtpFlag.ALLERGIC_REACTION, r"allergic reaction|anaphyla\w+|(?:swelling|swollen|rash|hives|itching)\b[^.;]{0,40}\bafter (?:eating|taking|an? (?:injection|sting|bite))", ()),
    (AtpFlag.AUDIBLE_WHEEZE, r"wheez\w*", ()),
    (AtpFlag.STRIDOR, r"stridor", ()),
    (AtpFlag.DANGEROUS_MECHANISM_TRAUMA, r"fall from (?:a |the )?(?:height|moving|roof|tree|terrace|building|bus|train|bike|motorcycle)|"
                                         r"fell from (?:a |the )?(?:height|moving|roof|tree|terrace|building|bus|train)|road traffic accident|\brta\b|"
                                         r"hit by (?:a |an )?(?:car|bus|truck|lorry|vehicle|bike|motorcycle|tractor)|run over by|"
                                         r"(?:bike|car|motorcycle|scooter|two-wheeler) accident", ()),
    (AtpFlag.LIMB_ISCHAEMIA_48H, r"(?:leg|foot|arm|hand|toes?|fingers?) (?:is |are |became |went |turned )?(?:cold and (?:pale|blue)|(?:pale|blue) and cold)", ()),
    (AtpFlag.SCROTAL_PAIN_YOUNG_MALE, r"(?:scrotal|testicular|testis) pain|pain in (?:the )?(?:scrotum|testis|testicles?)", ()),
    (AtpFlag.AGITATED_VIOLENT, r"\bviolent\b|combative|very agitated|aggressive and", ()),
]

# Two-part flags: the phrase must be present AND the context phrase somewhere in the case (any segment); the context
# quote is added as a second evidence entry. Context is checked for negation the same way.
_PREG_LATE = r"(?:2[89]|3\d|4[0-2])\s*weeks?(?: pregnant| of pregnancy| gestation)?|third trimester|(?:7|8|9|seven|eight|nine)(?:th)? months? pregnant"
_IMMUNO = r"\bhiv\b|on chemo(?:therapy)?|chemotherapy|transplant|on steroids|immunocompromised"
CONTEXT_RULES: list[tuple[AtpFlag, str, str]] = [
    (AtpFlag.THIRD_TRIMESTER_PAIN_OR_BLEEDING, rf"vaginal bleeding|bleeding (?:per vaginum|p/?v)|{_BODY} pain|labou?r pains?", _PREG_LATE),
    (AtpFlag.ABD_PAIN_WITH_VAGINAL_BLEEDING, rf"{_BODY} pain\b[^.;]{{0,40}}\bvaginal bleeding|vaginal bleeding\b[^.;]{{0,40}}\b{_BODY} pain", r"vaginal bleeding"),
    (AtpFlag.FEVER_IMMUNOCOMPROMISED, r"fever|febrile", _IMMUNO),
]

_COMPILED = [(flag, re.compile(p, _I), extra) for flag, p, extra in RULES]
_CONTEXT = [(flag, re.compile(p, _I), re.compile(c, _I)) for flag, p, c in CONTEXT_RULES]
# Clause boundaries for the negation scope, the same set as grounding (app.ai.grounding._CLAUSE_BREAK): "and"/"with"
# end it (so "no fever and chest pain" keeps chest pain), "or" does not ("denies fainting or seizures" negates both).
# A hit spanning a boundary ("lips and face swelling") is still one hit.
_BREAK = re.compile(r"[.;:!?,\n]|\b(?:but|and|with|although|though|except)\b", _I)
_SENTENCE = re.compile(r"[.;!?\n]")
_PAST = re.compile(r"\b(?:\d+|one|two|three|four|five|six|several|many)\s+(?:years?|months?)\s+(?:ago|back)\b|\bhistory of\b|\bh/o\b|\bin the past\b|\bknown case of\b", _I)
_LEADING_CUE_WORDS = 2
MAX_QUOTE = 300


@dataclass(frozen=True)
class Hit:
    flag: str
    segment_id: str
    quote: str
    flags: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]  # (segment_id, quote); the first is the phrase, a second the context


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    left = max((m.end() for m in _BREAK.finditer(text, 0, start)), default=0)
    right = next((m.start() for m in _BREAK.finditer(text, end)), len(text))
    return left, right


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    left = max((m.end() for m in _SENTENCE.finditer(text, 0, start)), default=0)
    right = next((m.start() for m in _SENTENCE.finditer(text, end)), len(text))
    return left, right


def _negation_state(text: str, start: int, end: int) -> str:
    """'negated' (cue opens the clause, before the phrase), 'conflict' (a cue elsewhere), or 'present'. The matched
    phrase itself is excluded, so "unable to pass urine" is not read as negated by its own words."""
    left, right = _clause(text, start, end)
    before = norm(text[left:start])
    after = norm(text[end:right])
    m = _NEGATION_RX.search(before)
    if m is not None and len(before[: m.start()].split()) <= _LEADING_CUE_WORDS:
        return "negated"
    return "conflict" if m is not None or _NEGATION_RX.search(after) else "present"


def _quote(text: str, start: int, end: int) -> str:
    s, e = _sentence(text, start, end)
    q = text[s:e].strip()
    if len(q) > MAX_QUOTE:
        q = text[start:end].strip()
    return q


def _find(segments: dict[str, str], rx: re.Pattern) -> list[tuple[str, int, int, str]]:
    """Non-negated matches as (segment_id, start, end, state)."""
    out = []
    for sid, text in segments.items():
        for m in rx.finditer(text):
            state = _negation_state(text, m.start(), m.end())
            if state != "negated":
                out.append((sid, m.start(), m.end(), state))
    return out


def _review_flags(text: str, start: int, end: int, state: str, extra: tuple[str, ...]) -> tuple[str, ...]:
    s, e = _sentence(text, start, end)
    flags = list(extra)
    if state == "conflict":
        flags.append("negation_conflict")
    if _PAST.search(text[s:e]):
        flags.append("past_history_cue")
    return tuple(sorted(set(flags)))


def suggest(segments: dict[str, str]) -> list[Hit]:
    """One hit per flag (the first matching phrase), in rule order. `segments` maps segment id → redacted text,
    exactly what the provider saw, so every quote grounds in the stored segment."""
    hits: dict[str, Hit] = {}
    for flag, rx, extra in _COMPILED:
        if flag.value in hits:
            continue
        found = _find(segments, rx)
        if found:
            sid, s, e, state = found[0]
            text = segments[sid]
            q = _quote(text, s, e)
            hits[flag.value] = Hit(flag.value, sid, q, _review_flags(text, s, e, state, extra), ((sid, q),))
    for flag, rx, ctx in _CONTEXT:
        if flag.value in hits:
            continue
        found, context = _find(segments, rx), _find(segments, ctx)
        if found and context:
            sid, s, e, state = found[0]
            csid, cs, ce, cstate = context[0]
            text, ctext = segments[sid], segments[csid]
            q, cq = _quote(text, s, e), _quote(ctext, cs, ce)
            flags = set(_review_flags(text, s, e, state, ())) | set(_review_flags(ctext, cs, ce, cstate, ()))
            evidence = ((sid, q),) if (sid, q) == (csid, cq) else ((sid, q), (csid, cq))
            hits[flag.value] = Hit(flag.value, sid, q, tuple(sorted(flags)), evidence)
    return list(hits.values())


def merge(fields: list, hits: list[Hit]) -> list:
    """Keyword fields to append to the voted model fields (docs/16 §2c). A hit for a flag the model already raised
    (non-negated) adds `keyword_rule_corroborated` to that model field instead of a duplicate. Otherwise — including
    when the model reported the flag as negated — the hit becomes its own `keyword_rule` field: raise-only, a negation
    from one source never hides an alarm from the other. Mutates the matching model fields' flags."""
    from app.ai.maker import VotedField

    raised = {f.key: f for f in fields if f.kind == "red_flag" and f.priority_review and not (f.value or {}).get("negated")}
    out = []
    for h in hits:
        key = f"red_flag:{h.flag}"
        if key in raised:
            raised[key].flags = sorted(set(raised[key].flags) | {"keyword_rule_corroborated"})
            continue
        out.append(VotedField(key, "red_flag", "keyword_suggested", None, {"flag": h.flag, "negated": False}, [],
                              [{"segment_id": sid, "quote": q} for sid, q in h.evidence], True, True, sorted(set(h.flags) | {"keyword_rule"}),
                              origin="keyword_rule"))
    return out
