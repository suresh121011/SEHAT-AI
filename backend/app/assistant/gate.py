"""The answer gate (docs/24 §3; plan §6 step 4). Every sentence the model writes is checked before a doctor sees it.
A failing sentence is dropped and counted with its reason, never repaired, and its text is never stored or shown.

The text is NFKC-normalised first (so superscript or full-width digits are digits). Then, in order:
1. the reply has the agreed shape, with at most 2 x MAX_SENTENCES sentences (otherwise nothing is shown:
   "unparseable"); sentences after MAX_SENTENCES are dropped;
2. each sentence: not empty, at most MAX_CHARS characters, at most MAX_CITES citations; it cites at least one item,
   and every cited item is one a tool returned to the model in this run;
3. brackets: only the exact unchecked tags (" [not yet checked]" and the like) may appear;
4. numbers: none written in words or Roman numerals; digits only from a cited fact's label or value or a non-fact
   item's short label. A sentence that cites a missing item or a question may use only those labels' numbers (so no
   value is given for something missing), and a removed value gives none;
5. cited facts: may be stated in prose; negation and timing clear; polarity: a sentence citing a denied fact cites
   only denied facts and reads "No X." or "X was denied." (strict form); a sentence citing a reported fact has no
   negation word at all ("no", "not", "free of", "resolved"...); each unchecked fact's tag is repeated; a removed item
   is stated as "no longer in the record";
6. assistant-only word lists, stricter than the shared guard: urgency; judgement and reassurance; diagnosis;
   treatment, medicine (including medicine names) and disposition;
7. the shared output and input guards (app/ai/guard.py);
8. item forms: a sentence citing a missing item, a pending check, a conflict or a follow-up topic cites only items of
   that one kind and reads exactly "X is not recorded." / "Not recorded: X, Y.", "X is not yet checked." /
   "Not yet checked: X.", "Sources disagree on X.", or "Ask about X." (an optional "First, / Next, / Then, /
   Also, / Finally," before it). So it can never say the opposite of its item ("SpO2 was recorded.");
9. naming: a sentence may name a symptom, a vital sign or any item of this visit's record (a server-side list, never
   sent to the model) only if a cited item carries that name, and each cited fact must be named. So "Chest pain
   reported." citing the cough fact, or "Saturation 94." citing the pulse, is dropped.

For facts, a check-state word ("checked", "confirmed", "verified", "reviewed", "measured"...) may appear only when
every cited fact was checked by a person and the sentence has no negation; otherwise only the tag says how far a value
was checked.

Before all of this, a sentence with an invisible format character or a non-ASCII letter (look-alike letters) is
dropped, so no word list can be dodged by a zero-width space or a Cyrillic "е".

A word list can never be complete: these checks catch the wording they list, not every way of saying it. The doctor
reads the cited items under each sentence.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from app.ai import encounter_summary as es
from app.ai import guard
from app.ai.schemas import SYMPTOM_TERMS
from app.services.visit_tools import Citable, main_term  # noqa: F401  (Citable re-exported for the assistant)

GATE_VERSION = "gate-2026-11-13.6"  # part of the reuse key: an answer passed by an older gate is never reused
MAX_SENTENCES = 6
MAX_CHARS = 300
MAX_CITES = 8

_I = re.IGNORECASE
URGENCY = re.compile(r"\b(?:red|yellow|green|urgent(?:ly)?|urgency|emergenc(?:y|ies)|emergent|priority|critical|serious(?:ly)?|stable|"
                     r"routine|non-urgent|immediate(?:ly)?|triage\s+(?:level|category|colou?r))\b", _I)
JUDGEMENT = re.compile(r"\b(?:normal|abnormal|well|fine|okay|ok|reassur\w*|alarm\w*|worr\w*|mild|moderate|severe|low|high|risk\w*|safe(?:ly)?|"
                       r"go(?:es)?\s+home|send\s+home|discharg\w*|wait|later|soon|benign|unremarkable|concern\w*|nothing\s+to|"
                       r"within\s+(?:normal\s+)?limits)\b", _I)
DIAGNOSTIC = re.compile(r"\b(?:(?:could|may|might|must|would)\s+be|probabl\w*|possibl\w*|likel\w*|unlikely|suspect\w*|consider\w*|"
                        r"rul(?:e|es|ed|ing)\s+out|viral|bacterial|diagnos\w*|condition|disease|disorder|syndrome|caus\w*|due\s+to|indicat\w*|"
                        r"suggest(?:s|ive|ing)?|points?\s+to|dengue|malaria|typhoid|sepsis|septic|stroke|tb|tuberculosis|pneumonia|covid|"
                        r"infarct\w*|heart\s+attack|appendicitis|meningitis|eclampsia|pre-?eclampsia|anaemi\w*|anemi\w*|diabet\w*|"
                        r"hypertensi\w*|infection|infected|asthma|copd|cancer|tumou?r|fracture\w*)\b", _I)
PRESCRIPTIVE = re.compile(r"\b(?:give|gives|giv(?:en|ing)|start\w*|stop\w*|administer\w*|prescri\w*|treat\w*|therap\w*|medicat\w*|medicine\w*|"
                          r"drug\w*|tablet\w*|capsule\w*|syrup\w*|inject\w*|dose\w*|dosage|antibiotic\w*|paracetamol|ibuprofen|ors|fluids?|iv|"
                          r"drip|oxygen|nebuli\w*|admit\w*|admission|refer\w*|transfer\w*|home|now|today|tonight|tomorrow|enough|"
                          r"begin\w*|began|commenc\w*|send\w*|sent|hospital\w*|ambulance|observ\w*|monitor\w*|shift\w*|arrang\w*|"
                          r"amoxicillin|ambroxol|metformin|insulin|aspirin|cetirizine|salbutamol|ondansetron|diclofenac|"
                          r"\w+(?:cillin|mycin|floxacin|azole|prazole|olol|pril|sartan|statin|dipine))\b", _I)
NUMBER_WORDS = re.compile(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                          r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|dozen|"
                          r"half|twice|once|double|triple|(?:twen|thir|for|fif|six|seven|eigh|nine)ties|(?:thir|four|fif|six|seven|eigh|nine)teens)\b", _I)
ROMAN = re.compile(r"\b[IVXLCDM]{2,}\b")  # case-sensitive: "XCIV", not "mix"
NEGATION = re.compile(r"\b(?:no|not|never|none|nil|denied|denies|deny|without|absent|negative|neither|nor|free\s+of|resolved|gone|"
                      r"stopped|cleared|disappeared|ruled\s+out|subsided|went\s+away|earlier\s+only)\b|n't", _I)
TAGS = sorted({v["tag"].strip() for v in es.PROVENANCE.values() if v["tag"]}, key=len, reverse=True)
_TAG_RX = re.compile("|".join(re.escape(t) for t in TAGS))
_DENIED_FORMS = (re.compile(r"(?:no|denied(?:\s+at\s+intake)?:?)\s+(.+)"), re.compile(r"(.+?)\s+(?:(?:was|were)\s+)?denied(?:\s+at\s+intake)?"))
_DENIED_REST = re.compile(r"[\s,]*(?:(?:and|or)[\s,]+|,[\s,]*)*")
_REPORTED_ENDINGS = (":present", ":mentioned", ":ticked")
CHECK_STATE = re.compile(r"\b(?:check(?:ed|s)?|confirm\w*|verif\w*|review\w*|accept\w*|validat\w*|measur\w*|sign(?:ed)?(?:\s+off)?|"
                         r"approv\w*|recorded\s+by|entered\s+by|document\w*|certain\w*|definite\w*|corroborat\w*|establish\w*|"
                         r"ascertain\w*|reliabl\w*|trustworth\w*|for\s+sure)\b", _I)
CHANGE_DENIAL = re.compile(r"\b(?:unchanged|same|still|remain\w*|as\s+before|no\s+change)\b", _I)
_LEAD = r"(?:(?:first|next|then|also|finally)[:,]?\s+)?"
_KIND_FORMS = {
    "missing": (re.compile(_LEAD + r"not\s+recorded:\s+(.+)"), re.compile(_LEAD + r"(.+?)\s+(?:is|are)\s+not\s+recorded")),
    "waiting": (re.compile(_LEAD + r"not\s+yet\s+checked:\s+(.+)"), re.compile(_LEAD + r"(.+?)\s+(?:is|are)\s+not\s+yet\s+checked")),
    "conflict": (re.compile(_LEAD + r"sources\s+disagree\s+on:?\s+(.+)"),),
    "question": (re.compile(_LEAD + r"ask\s+about:?\s+(.+)"),),
}
VITAL_TERMS = ("temperature", "temp", "spo2", "sats", "saturation", "oxygen saturation", "pulse", "heart rate", "respiratory rate",
               "breathing rate", "respiration", "breathing", "blood pressure", "bp", "systolic", "diastolic", "consciousness", "gcs",
               "acvpu", "weight", "height", "haemoglobin", "hemoglobin", "hb", "blood sugar", "glucose")
LEXICON = frozenset(SYMPTOM_TERMS) | frozenset(VITAL_TERMS)
_ALLOWED_NON_ASCII_LETTERS = frozenset("μ")  # the micro sign after NFKC


def _phrase_in(term: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def _unexpected_character(text: str) -> bool:
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf" or (cat.startswith("L") and not ch.isascii() and ch not in _ALLOWED_NON_ASCII_LETTERS):
            return True
    return False


def _names_only(rest: str, labels) -> bool:
    for label in sorted({x.lower() for x in labels}, key=len, reverse=True):
        rest = rest.replace(label, " ")
    return _DENIED_REST.fullmatch(rest) is not None


def _kind_form(text: str, items: list[Citable]) -> bool:
    """A sentence about missing items, pending checks, conflicts or follow-up topics: one kind, one fixed form."""
    kinds = {i.kind for i in items}
    if not kinds & set(_KIND_FORMS):
        return True
    if len(kinds) != 1:
        return False
    t = " ".join(text.lower().rstrip(". ").split())
    for rx in _KIND_FORMS[next(iter(kinds))]:
        m = rx.fullmatch(t)
        if m and _names_only(m.group(1), [i.label for i in items]):
            return True
    return False


def _check_state(text: str, facts) -> bool:
    """True when a sentence claims a check state its facts do not have."""
    t = _TAG_RX.sub(" ", text)
    if not facts or not CHECK_STATE.search(t):
        return False
    return any(f.trust != "checked" for f in facts) or bool(NEGATION.search(t))


def _naming(text: str, items: list[Citable], facts, terms: frozenset[str]) -> str | None:
    """A named record item or vital must be cited; a cited fact must be named."""
    lower = text.lower()
    covered = " ".join([i.label.lower() for i in items] + [f.label.lower() for f in facts])
    if any(_phrase_in(t, lower) and not _phrase_in(t, covered) for t in LEXICON | terms):
        return "uncited_item_named"
    if any(not _phrase_in(main_term(f.label), lower) for f in facts):
        return "cited_item_not_named"
    return None


class _Sentence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    cites: list[str]


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sentences: list[_Sentence]


@dataclass
class Checked:
    status: str  # "answered", "unparseable" or "tool_limit"
    kept: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


def _allowed_numbers(items: list[Citable]) -> set[str]:
    labels_only = any(i.kind in ("missing", "question") for i in items)  # never a value for something missing
    out: set[str] = set()
    for i in items:
        if i.removed:
            continue
        if i.facts and not labels_only:
            for f in i.facts:
                out |= es.numbers_in(f"{f.label} {f.display}")
        elif not i.facts:
            out |= es.numbers_in(i.label)
    return out


def _denied_form(text: str, denied) -> bool:
    """A sentence about denied facts must read "No X." / "Denied: X, Y." / "X and Y were denied." and name only them."""
    t = " ".join(_TAG_RX.sub(" ", text).lower().rstrip(". ").split())
    for rx in _DENIED_FORMS:
        m = rx.fullmatch(t)
        if m:
            rest = m.group(1)
            for label in sorted({f.label.lower() for f in denied}, key=len, reverse=True):
                rest = rest.replace(label, " ")
            if _DENIED_REST.fullmatch(rest):
                return True
    return False


def _polarity(text: str, items: list[Citable], facts) -> bool:
    """True when the sentence could state a denied fact as reported, or a reported one as absent."""
    denied = [f for f in facts if f.key.endswith(":denied")]
    if denied:
        only_denied = all(i.facts and all(f.key.endswith(":denied") for f in i.facts) for i in items)
        return not (only_denied and _denied_form(text, denied))
    reported = any(f.key.endswith(_REPORTED_ENDINGS) for f in facts)
    return reported and bool(NEGATION.search(_TAG_RX.sub(" ", text)))


def _reason(raw_text: str, cites: list[str], citable: dict[str, Citable], terms: frozenset[str]) -> str | None:
    text = unicodedata.normalize("NFKC", raw_text)
    if not text.strip():
        return "empty"
    if _unexpected_character(text):
        return "unexpected_character"
    if len(text) > MAX_CHARS:
        return "too_long"
    if len(cites) > MAX_CITES:
        return "too_many_citations"
    if not cites or any(c not in citable for c in cites):
        return "uncited_or_unknown_item"
    if "[" in _TAG_RX.sub(" ", text) or "]" in _TAG_RX.sub(" ", text):
        return "unexpected_bracket"
    if NUMBER_WORDS.search(text) or ROMAN.search(text):
        return "number_in_words"
    items = [citable[c] for c in cites]
    if not es.numbers_in(text) <= _allowed_numbers(items):
        return "number_not_in_cited_items"
    facts = [f for i in items for f in i.facts]
    if any(not es.PROVENANCE[f.provenance]["narrative"] for f in facts):
        return "unchecked_document_value_in_prose"
    if any(es.uncertain(f) for f in facts):
        return "negation_or_time_uncertain"
    if es._polarity_mismatch(text, facts) or _polarity(text, items, facts):  # noqa: SLF001  (the summary's own rule, shared on purpose)
        return "polarity_mismatch"
    if es._missing_tag(text, facts):  # noqa: SLF001
        return "missing_unchecked_tag"
    if _check_state(text, facts):
        return "check_state_mismatch"
    if any(i.removed for i in items) and "no longer" not in text.lower():
        return "removed_value_as_current"
    if any(i.kind == "change" for i in items) and CHANGE_DENIAL.search(text):
        return "change_contradicted"
    if URGENCY.search(text):
        return "urgency_statement"
    if JUDGEMENT.search(text):
        return "judgement_word"
    if DIAGNOSTIC.search(text):
        return "diagnostic_language"
    if PRESCRIPTIVE.search(text):
        return "prescriptive_language"
    if reason := guard.check_output(text) or guard.check_input(text):
        return reason
    if not _kind_form(text, items):
        return "item_form_mismatch"
    return _naming(text, items, facts, terms)


def check(raw: str, citable: dict[str, Citable], terms: frozenset[str] = frozenset()) -> Checked:
    """`terms`: what this visit's record names (VisitTools.server_terms); the closed symptom and vital lists always apply."""
    try:
        answer = _Answer.model_validate_json(raw)
    except (ValidationError, ValueError):
        return Checked("unparseable")
    if len(answer.sentences) > 2 * MAX_SENTENCES:  # far over the limit: not the agreed answer, nothing is shown
        return Checked("unparseable")
    out = Checked("answered")
    for n, s in enumerate(answer.sentences):
        known = [c for c in s.cites if c in citable][:MAX_CITES]
        reason = "too_many_sentences" if n >= MAX_SENTENCES else _reason(s.text, s.cites, citable, terms)
        if reason:
            out.dropped.append({"reason": reason, "cites": known})
        else:
            out.kept.append({"text": s.text, "cites": [{"id": c, "kind": citable[c].kind, "label": citable[c].label} for c in s.cites]})
    return out
