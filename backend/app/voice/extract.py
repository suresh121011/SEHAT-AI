"""Deterministic critical-value extractor for voice transcripts (docs/12 §5).

Finds candidate measurements (temperature, SpO2, pulse, respiratory rate, blood pressure, age,
pregnancy, symptom duration) in an *untrusted* machine transcript so each can be read back and
explicitly confirmed or corrected by the health worker. It never decides urgency, never infers
symptoms or red flags, and never fills a value on its own: every candidate needs a read-back decision.

Design rules (council-reviewed):
- Offsets index the raw transcript exactly, so the reviewer can see the highlighted span. Only a
  length-preserving normalisation is applied (Unicode digits → ASCII digits, per-character lowercase).
- Numbers are digit tokens (any script), English number words, and Hindi/Odia number words from a
  closed table (Hindi 0–100 with सौ; Odia 0–99 with ଶହ and a leading ଶହେ = 100; 21–99 and the alternate
  spellings are copied from Unicode CLDR RBNF, see _OR_CLDR). The Hindi/Odia tables are
  `draft_unreviewed`: CLDR is a written standard, not a native-speaker review of what an ASR model writes. Any number word outside the tables is not parsed: that value stays missing and
  needs human review. Candidates built from Indic number words carry the `number_words` flag.
- Keyword lists for Hindi/Odia are `draft_unreviewed` (not reviewed by native speakers).
- Units follow the rules engine: temperature in °C only. °F is converted exactly, (f − 32) × 5/9,
  and never rounded (rounding can move a value across the ATP ">39 °C" boundary).
- Unit inference uses only the engine's own domain: 25–45 → °C, 77–113 → °F (the °F image of 25–45).
  Anything else is `unit_unknown` and is not converted. No new clinical thresholds are introduced.
- Ranges checked are the existing `Vitals` bounds (app/rules/models.py); nothing stricter.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

Field = Literal["temp", "spo2", "pulse", "resp_rate", "bp", "age", "pregnancy", "symptom_duration", "unassigned"]
Unit = Literal["c", "f", "percent", "per_min", "mmhg", "years", "months", "weeks", "days", "hours"]
Flag = Literal[
    "unit_inferred",
    "unit_unknown",
    "out_of_domain_range",
    "non_integer",
    "decimal_ambiguity",
    "negation",
    "uncertainty",
    "temporal_reference",
    "multiple_values",
    "oxygen_context",
    "bp_order_invalid",
    "age_unit_months",
    "needs_assignment",
    "number_words",
    "number_modifier_unparsed",
    "number_sequence_ambiguous",
    "unit_unclear",
    "number_word_homograph",
    "context_unclear",
    "bp_shorthand_possible",
]

KEYWORD_REVIEW_STATUS = {"en": "project_draft", "hi": "draft_unreviewed", "or": "draft_unreviewed"}

# Engine bounds (mirror app/rules/models.py Vitals + TriageInput.age_years). Kept here as plain data so
# the extractor has no dependency on the rules package; tests assert they match.
BOUNDS = {
    "temp_c": (25.0, 45.0),
    "spo2": (0, 100),
    "pulse": (0, 300),
    "resp_rate": (0, 80),
    "sbp": (0, 300),
    "dbp": (0, 200),
    "age_years": (0, 120),
}
F_RANGE = (77.0, 113.0)  # exactly the °F image of the engine's 25–45 °C domain

# ── Lexicons (lower-case; Latin entries are matched on word boundaries) ─────────────────────────
KEYWORDS: dict[str, tuple[str, ...]] = {
    "temp": ("temperature", "temp", "fever", "bukhar", "bukhaar", "taapmaan", "tapman", "jwar", "jwara",
             "तापमान", "बुखार", "ज्वर", "ତାପମାତ୍ରା", "ତାପମାନ", "ଜ୍ୱର", "ଜ୍ବର"),
    "spo2": ("spo2", "sp02", "oxygen", "saturation", "sats", "oximeter", "ऑक्सीजन", "ଅକ୍ସିଜେନ", "ଅମ୍ଳଜାନ"),
    "pulse": ("pulse", "heart rate", "heartbeat", "heart beat", "nabz", "nadi", "dhadkan",
              "नब्ज़", "नब्ज", "नब्स", "नाड़ी",  # नब्स: spelling observed in real IndicConformer output
              "नाडी", "धड़कन", "ନାଡ଼ି", "ନାଡି", "ହୃଦସ୍ପନ୍ଦନ"),
    "resp_rate": ("breathing", "breaths", "breath rate", "respiratory rate", "respiration", "resp rate",
                  "saans", "sans", "साँस", "सांस", "श्वास", "ଶ୍ୱାସ", "ନିଶ୍ୱାସ"),
    "bp": ("bp", "blood pressure", "pressure", "रक्तचाप", "ब्लड प्रेशर", "बीपी", "ରକ୍ତଚାପ", "ବିପି"),
    "age": ("age", "aged", "years old", "year old", "umar", "umr", "उम्र", "आयु", "ବୟସ"),
    "pregnancy": ("pregnant", "pregnancy", "garbhvati", "garbhavati", "गर्भवती", "गर्भ", "ଗର୍ଭବତୀ", "ଗର୍ଭ"),
}
UNIT_WORDS: dict[str, tuple[str, ...]] = {
    "f": ("°f", "° f", "degrees fahrenheit", "degree fahrenheit", "fahrenheit", "f"),
    "c": ("°c", "° c", "degrees celsius", "degree celsius", "celsius", "centigrade", "c"),
    "degree": ("degrees", "degree", "deg", "°", "डिग्री", "ଡିଗ୍ରୀ", "ଡିଗ୍ରି"),
    "percent": ("%", "percent", "per cent", "प्रतिशत", "ପ୍ରତିଶତ"),
    "per_min": ("per minute", "/min", "a minute", "per min", "प्रति मिनट", "ପ୍ରତି ମିନିଟ"),
    "years": ("years", "year", "yrs", "yr", "saal", "sal", "varsh", "साल", "वर्ष", "ବର୍ଷ"),
    "months": ("months", "month", "mahine", "mahina", "maheene", "महीने", "महीना", "मास", "ମାସ"),
    "weeks": ("weeks", "week", "hafte", "hafta", "हफ्ते", "हफ़्ते", "सप्ताह", "ସପ୍ତାହ"),
    "days": ("days", "day", "din", "दिन", "ଦିନ"),
    "hours": ("hours", "hour", "hrs", "ghante", "ghanta", "घंटे", "घंटा", "ଘଣ୍ଟା"),
}
NEGATION = ("no", "not", "never", "without", "denies", "deny", "don't", "doesn't", "didn't", "isn't", "none",
            "nahi", "nahin", "nai", "नहीं", "नही", "ना", "न", "ନାହିଁ", "ନାହିଁ", "ନାହି", "ନୁହେଁ", "ନା")
UNCERTAINTY = ("maybe", "probably", "might", "around", "about", "approximately", "roughly", "not sure",
               "shayad", "lagbhag", "karib", "kareeb", "शायद", "लगभग", "करीब", "क़रीब", "ପ୍ରାୟ", "ବୋଧହୁଏ", "ହୁଏତ")
TEMPORAL = ("yesterday", "last night", "earlier", "before", "last week", "was", "were", "had been", "used to", "ago", "last",
            "kal", "pehle", "pahle", "tha", "thi", "parson", "pichhle", "कल", "पहले", "था", "थी", "थे", "परसों", "पिछले", "पिछली",
            "ଗତକାଲି", "କାଲି", "ପୂର୍ବରୁ", "ଆଗରୁ", "ଥିଲା", "ଗତ", "ପରଶୁ")
OXYGEN_CONTEXT = ("on oxygen", "oxygen support", "oxygen mask", "mask", "cylinder", "concentrator", "room air",
                  "without oxygen", "with oxygen", "nasal", "सिलेंडर", "मास्क", "ସିଲିଣ୍ଡର", "ମାସ୍କ")
AGE_OLD_MARKERS = ("old", "saal ka", "saal ki", "sal ka", "sal ki", "साल का", "साल की", "ବର୍ଷର", "ବର୍ଷ ବୟସ")

# "Word character" for boundaries: \w plus the whole Devanagari and Odia blocks, so vowel signs and
# viramas (not \w in Python) never create a false boundary inside a word.
_WORDCH = r"\w\u0900-\u097F\u0B00-\u0B7F"
_LB = rf"(?<![{_WORDCH}])"
_RB = rf"(?![{_WORDCH}])"

# Clause boundaries: sentence punctuation (incl. danda) and common conjunctions as whole words.
# Negation and keyword scope never crosses a clause boundary.
_CLAUSE_SPLIT = re.compile(rf"[.;!?।\n,]|{_LB}(?:and|but|also|aur|lekin|par|और|लेकिन|पर|ଏବଂ|କିନ୍ତୁ|ଓ){_RB}")

_EN_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
             "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_EN_ALL = set(_EN_UNITS) | set(_EN_TENS) | {"hundred", "a", "and", "point", "oh"}


@dataclass
class Candidate:
    field: Field
    char_start: int
    char_end: int
    raw_value: float | None
    raw_value2: float | None = None  # diastolic for bp
    unit: Unit | None = None
    normalized: dict[str, float | int | bool] | None = None  # engine-shaped value, or None if not prefillable
    flags: list[Flag] = field(default_factory=list)
    unit_source: Literal["spoken", "inferred", "none"] = "none"

    def add(self, flag: Flag) -> None:
        if flag not in self.flags:
            self.flags.append(flag)


@dataclass
class _Num:
    value: float
    start: int
    end: int
    has_decimal: bool
    digits: int
    words: bool = False  # built from Hindi/Odia number words (closed draft table)


# ── Normalisation (length-preserving) ────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    out = []
    for ch in text:
        if unicodedata.category(ch) == "Nd":
            out.append(str(unicodedata.decimal(ch)))
            continue
        low = ch.lower()
        out.append(low if len(low) == 1 else ch)
    folded = "".join(out)
    assert len(folded) == len(text)
    return folded


def _is_latin_word(term: str) -> bool:
    return all(ord(c) < 128 for c in term)


def _find_terms(text: str, terms: tuple[str, ...], lo: int = 0, hi: int | None = None) -> list[tuple[int, int]]:
    """Occurrences of any term inside text[lo:hi]; Latin terms must sit on word boundaries."""
    hi = len(text) if hi is None else hi
    hits: list[tuple[int, int]] = []
    for term in terms:
        left = _LB if term[0].isalnum() or not _is_latin_word(term) else ""
        # Indic terms of 3+ code points may take inflection suffixes (e.g. बुखार → बुखारों); short
        # ones (न, ना, ନା) and all Latin words must end on a boundary.
        right = "" if (not _is_latin_word(term) and len(term) >= 3) or not term[-1].isalnum() and _is_latin_word(term) else _RB
        pattern = re.compile(left + re.escape(term) + right)
        for m in pattern.finditer(text, lo, hi):
            hits.append((m.start(), m.end()))
    return hits


# ── Numbers ──────────────────────────────────────────────────────────────────────────────────────


def _digit_numbers(text: str) -> list[_Num]:
    nums = []
    for m in re.finditer(r"(?<![\d.a-z])(\d+(?:\.\d+)?)(?![\d])", text):
        s = m.group(1)
        nums.append(_Num(float(s), m.start(1), m.end(1), "." in s, len(s.replace(".", ""))))
    return nums


def _word_numbers(text: str) -> list[_Num]:
    """English number words, e.g. 'one hundred and two point four', 'ninety eight'."""
    tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[a-z]+", text)]
    nums: list[_Num] = []
    i = 0
    while i < len(tokens):
        if tokens[i][0] not in _EN_UNITS and tokens[i][0] not in _EN_TENS and not (tokens[i][0] == "a" and i + 1 < len(tokens) and tokens[i + 1][0] == "hundred"):
            i += 1
            continue
        j = i
        total, current, seen_number = 0, 0, False
        decimal_digits: list[int] = []
        in_decimal = False
        last_end = tokens[i][2]
        prev_kind = None  # "unit" (0-19), "tens", "hundred": only tens→unit(1-9), unit→hundred, hundred→any combine
        while j < len(tokens) and tokens[j][0] in _EN_ALL:
            word = tokens[j][0]
            if j > i and tokens[j][1] - last_end > 2:  # words must be adjacent
                break
            if in_decimal:
                if word in _EN_UNITS and _EN_UNITS[word] < 10:
                    decimal_digits.append(_EN_UNITS[word])
                elif word == "oh":
                    decimal_digits.append(0)
                else:
                    break
            elif word == "point":
                if not seen_number or j + 1 >= len(tokens) or tokens[j + 1][0] not in _EN_UNITS:
                    break
                in_decimal = True
            elif word == "a":
                if j + 1 < len(tokens) and tokens[j + 1][0] == "hundred" and not seen_number:
                    current = 1
                else:
                    break
            elif word == "and":
                if prev_kind != "hundred" or j + 1 >= len(tokens) or (tokens[j + 1][0] not in _EN_UNITS and tokens[j + 1][0] not in _EN_TENS):
                    break
            elif word == "hundred":
                if prev_kind not in (None, "unit") or (prev_kind == "unit" and current >= 10):
                    break
                current = (current or 1) * 100
                seen_number, prev_kind = True, "hundred"
            elif word in _EN_TENS:
                if prev_kind not in (None, "hundred"):
                    break  # "one twenty", "twenty thirty": separate numbers, never summed
                current += _EN_TENS[word]
                seen_number, prev_kind = True, "tens"
            elif word in _EN_UNITS:
                value_w = _EN_UNITS[word]
                if prev_kind == "unit" or (prev_kind == "tens" and value_w >= 10) or (prev_kind == "tens" and value_w == 0):
                    break  # "nine eight": digit-by-digit, never summed
                current += value_w
                seen_number, prev_kind = True, "unit"
            elif word == "oh":
                break
            last_end = tokens[j][2]
            j += 1
        if seen_number:
            value = float(total + current)
            if decimal_digits:
                value += float("0." + "".join(map(str, decimal_digits)))
            nums.append(_Num(value, tokens[i][1], last_end, bool(decimal_digits), len(str(int(value)))))
        i = max(j, i + 1)
    return nums



# ── Hindi / Odia number words (closed tables, draft_unreviewed) ─────────────────────────────────
_HI_WORDS = (
    "शून्य एक दो तीन चार पांच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
    "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस "
    "पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस "
    "सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरेपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ "
    "बासठ तिरेसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर "
    "छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी "
    "नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()
_HI_VARIANTS = {"पाँच": 5, "छः": 6, "छे": 6, "पन्द्रह": 15, "अड़तीस": 38, "छियालिस": 46,
                # Alternate spellings from Unicode CLDR common/rbnf/hi.xml (accessed 2026-10-01)
                "चौवालीस": 44, "उनासी": 79, "इक्यानबे": 91, "बानबे": 92, "तिरानबे": 93, "चौरानबे": 94,
                "पंचानबे": 95, "छियानबे": 96, "सत्तानबे": 97, "अट्ठानबे": 98, "निन्यानबे": 99}
_OR_WORDS = {"ଶୂନ": 0, "ଶୂନ୍ୟ": 0, "ଏକ": 1, "ଦୁଇ": 2, "ତିନି": 3, "ଚାରି": 4, "ପାଞ୍ଚ": 5, "ଛଅ": 6, "ସାତ": 7, "ଆଠ": 8,
             "ନଅ": 9, "ଦଶ": 10, "ଏଗାର": 11, "ବାର": 12, "ତେର": 13, "ଚଉଦ": 14, "ପନ୍ଦର": 15, "ଷୋହଳ": 16, "ସତର": 17,
             "ଅଠର": 18, "ଊଣେଇଶି": 19, "କୋଡ଼ିଏ": 20, "କୋଡିଏ": 20, "ତିରିଶ": 30, "ଚାଳିଶ": 40, "ପଚାଶ": 50, "ଷାଠିଏ": 60,
             "ସତୁରି": 70, "ଅଶୀ": 80, "ନବେ": 90}
# Odia 16–99 exactly as spelled in Unicode CLDR common/rbnf/or.xml, %spellout-cardinal (accessed
# 2026-10-01; Unicode-DFS-2016 licence). Adds 21–99 and the CLDR spellings of 16, 19 and 70 next to the
# forms above. Spellings an ASR model writes differently are not guessed: they stay unparsed.
_OR_CLDR = {
    "ଷୋଳ": 16, "ଊଣେଇଶ": 19, "ଏକୋଇଶ": 21, "ବାଇଶ": 22, "ତେଇଶ": 23, "ଚଉବିଶ": 24, "ପଚିଶ": 25, "ଛବିଶ": 26,
    "ସତାଇଶ": 27, "ଅଠାଇଶ": 28, "ଊଣାତିରିଶ": 29, "ଏକତିରିଶ": 31, "ବତିଶ": 32, "ତେତିଶ": 33, "ଚଉତିରିଶ": 34,
    "ପଞ୍ଚତିରିଶ": 35, "ଛତିଶ": 36, "ସତତିରିଶ": 37, "ଅଠତିରିଶ": 38, "ଊଣଚାଳିଶ": 39, "ଏକଚାଳିଶ": 41, "ବୟାଳିଶ": 42,
    "ତେତାଳିଶ": 43, "ଚଉରାଳିଶ": 44, "ପଞ୍ଚଚାଳିଶ": 45, "ଛେଚାଳିଶ": 46, "ସତଚାଳିଶ": 47, "ଅଠଚାଳିଶ": 48, "ଊଣପଚାଶ": 49,
    "ଏକାବନ": 51, "ବାବନ": 52, "ତେପନ": 53, "ଚଉବନ": 54, "ପଞ୍ଚାବନ": 55, "ଛପନ": 56, "ସତାବନ": 57, "ଅଠାବନ": 58,
    "ଊଣଷାଠିଏ": 59, "ଏକଷଠି": 61, "ବାଷଠି": 62, "ତେଷଠି": 63, "ଚଉଷଠି": 64, "ପଞ୍ଚଷଠି": 65, "ଛଅଷଠି": 66,
    "ସତଷଠି": 67, "ଅଠଷଠି": 68, "ଊଣସତୁରୀ": 69, "ସତୁରୀ": 70, "ଏକସତୁରୀ": 71, "ବାସତୁରୀ": 72, "ତେସତୁରୀ": 73,
    "ଚଉସତୁରୀ": 74, "ପଞ୍ଚସତୁରୀ": 75, "ଛଅସତୁରୀ": 76, "ସତସତୁରୀ": 77, "ଅଠସତୁରୀ": 78, "ଊଣାଶୀ": 79, "ଏକାଶୀ": 81,
    "ବୟାଶୀ": 82, "ତେରାଶୀ": 83, "ଚଉରାଶୀ": 84, "ପଞ୍ଚାଶୀ": 85, "ଛିଆଶୀ": 86, "ସତାଶୀ": 87, "ଅଠାଶୀ": 88,
    "ଊଣାନବେ": 89, "ଏକାନବେ": 91, "ବୟାନବେ": 92, "ତେରାନବେ": 93, "ଚଉରାନବେ": 94, "ପଞ୍ଚାନବେ": 95, "ଛିଆନବେ": 96,
    "ସତାନବେ": 97, "ଅଠାନବେ": 98, "ନିଆଁନବେ": 99,
}
INDIC_NUMBER_WORDS: dict[str, int] = {**{w: i for i, w in enumerate(_HI_WORDS)}, **_HI_VARIANTS, **_OR_WORDS, **_OR_CLDR}
# CLDR: "100: ଶହେ[ >>]" — ଶହେ alone means one hundred and only starts a number (ଶହେ ଦୁଇ = 102). After a
# multiplier CLDR uses ଶହ (ଦୁଇ ଶହ). "ଦୁଇ ଶହେ" is therefore not parsed as 200: the units word is
# blocked as incomplete instead (number_modifier_unparsed), and ଶହେ there yields no number.
_OR_LEADING_HUNDRED = "ଶହେ"
# Table words that also mean something else in ordinary speech (ବାର: "twelve", but also "time(s)" as in
# ଅନେକ ବାର "many times"), and count words that turn the number before them into a count (एक बार "once").
# Either makes a value unsafe to accept with one click.
_HOMOGRAPH_NUMBER_WORDS = {"ବାର"}
# A lone "one" word is also the article "a" (ନାଡ଼ି ଏକ ଟିକେ ବେଶୀ "pulse a little high", "pulse is one of …").
_LONE_ONE_WORDS = {"ଏକ", "एक", "one"}
_COUNT_WORDS = ("बार", "ବାର", "ଥର", "दफा", "दफ़ा", "times", "time")
_INDIC_HUNDRED = {"सौ", "ଶହ"}
_INDIC_POINT = {"दशमलव", "पॉइंट", "प्वाइंट", "ଦଶମିକ", "ପଏଣ୍ଟ"}
# Fraction/quantity modifiers that change a following number (साढ़े उनतालीस = 39.5). They are NOT
# computed: a number next to one is flagged and cannot be confirmed as heard (must be corrected).
_NUMBER_MODIFIERS = {"साढ़े", "साढे", "सवा", "पौने", "डेढ़", "डेढ", "ढाई", "आधा", "आधे",
                     "ସାଢ଼େ", "ସାଢେ", "ସୱା", "ସଓା", "ପାଉଣେ", "ଦେଢ଼", "ଦେଢ", "ଅଢ଼େଇ", "ଅଧା",
                     "half", "quarter"}
_INDIC_TOKEN = re.compile(r"[\u0900-\u097F\u0B00-\u0B7F]+")
# A decimal point word or "." right after a parsed number that did not become part of it, plus the digit
# or number word after it (so the reviewer sees the whole span).
_SPLIT_DECIMAL = re.compile(
    rf"\s*(?:\.\s*\d+|{_LB}(?:point|dot|दशमलव|पॉइंट|प्वाइंट|ଦଶମିକ|ପଏଣ୍ଟ){_RB}(?:\s*(?:\d+|[a-z\u0900-\u097F\u0B00-\u0B7F]+))?)"
)


_HUNDRED_SUFFIXES = ("ଶହ", "सौ")


def _joined_hundred(token: str) -> int | None:
    """ASR sometimes writes 'two-hundred' as one word (ଦୁଇଶହ, दोसौ). Accept it only when the prefix is
    a known units word; any other token containing ଶହ/सौ is treated as unknown (and flagged)."""
    for suffix in _HUNDRED_SUFFIXES:
        if token.endswith(suffix) and token != suffix:
            prefix = INDIC_NUMBER_WORDS.get(token[: -len(suffix)])
            if prefix is not None and 1 <= prefix <= 9:
                return prefix * 100
    return None


def _indic_key(token: str) -> str:
    # Spelling variation from ASR: chandrabindu → anusvara (पाँच/पांच) is handled by the variants table;
    # nukta is kept (ड़ is distinct in the table). Exact match only — unknown words are never guessed.
    return token


def _indic_word_numbers(text: str) -> list[_Num]:
    """Closed-table parser: [units] [सौ/ଶହ] [units] [दशमलव digit…]. e.g. एक सौ दो → 102, ଏକ ଶହ ଦୁଇ → 102."""
    tokens = [(m.group(0), m.start(), m.end()) for m in _INDIC_TOKEN.finditer(text)]
    nums: list[_Num] = []
    i = 0
    while i < len(tokens):
        word = _indic_key(tokens[i][0])
        joined = _joined_hundred(word)
        if word == _OR_LEADING_HUNDRED:
            follows_number = i > 0 and tokens[i - 1][0] in INDIC_NUMBER_WORDS and tokens[i][1] - tokens[i - 1][2] <= 2
            joined = None if follows_number else 100  # "ଦୁଇ ଶହେ": no number from ଶହେ (see _OR_LEADING_HUNDRED)
            if joined is None:
                i += 1
                continue
        elif word not in INDIC_NUMBER_WORDS and word not in _INDIC_HUNDRED and joined is None:
            i += 1
            continue
        j, value, seen, last_end = i, 0, False, tokens[i][2]
        decimals: list[int] = []
        # leading units, or a joined hundred (ଦୁଇଶହ) / leading ଶହେ that is followed by optional units
        if joined is not None:
            value, seen, j, last_end = joined, True, i + 1, tokens[i][2]
            if j < len(tokens) and tokens[j][0] in INDIC_NUMBER_WORDS and tokens[j][1] - last_end <= 2:
                value += INDIC_NUMBER_WORDS[tokens[j][0]]
                last_end, j = tokens[j][2], j + 1
        elif word in INDIC_NUMBER_WORDS:
            value, seen, j = INDIC_NUMBER_WORDS[word], True, i + 1
            last_end = tokens[i][2]
        # hundred
        if j < len(tokens) and tokens[j][0] in _INDIC_HUNDRED and tokens[j][1] - last_end <= 2:
            value = (value if seen else 1) * 100
            seen, last_end, j = True, tokens[j][2], j + 1
            if j < len(tokens) and tokens[j][0] in INDIC_NUMBER_WORDS and tokens[j][1] - last_end <= 2:
                value += INDIC_NUMBER_WORDS[tokens[j][0]]
                last_end, j = tokens[j][2], j + 1
        # decimal part: point followed by single digits
        if seen and j + 1 < len(tokens) and tokens[j][0] in _INDIC_POINT and tokens[j][1] - last_end <= 2:
            k = j + 1
            while k < len(tokens) and INDIC_NUMBER_WORDS.get(tokens[k][0], 99) < 10 and tokens[k][1] - (tokens[k - 1][2]) <= 2:
                decimals.append(INDIC_NUMBER_WORDS[tokens[k][0]])
                k += 1
            if decimals:
                last_end, j = tokens[k - 1][2], k
        if not seen:
            i += 1
            continue
        number = float(value) + (float("0." + "".join(map(str, decimals))) if decimals else 0.0)
        nums.append(_Num(number, tokens[i][1], last_end, bool(decimals), len(str(value)), words=True))
        i = max(j, i + 1)
    return nums


def _numbers(text: str) -> list[_Num]:
    return sorted(_digit_numbers(text) + _word_numbers(text) + _indic_word_numbers(text), key=lambda n: n.start)


# ── Clauses and context ──────────────────────────────────────────────────────────────────────────


def _clauses(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in _CLAUSE_SPLIT.finditer(text):
        if m.start() > start:
            bounds.append((start, m.start()))
        start = m.end()
    if start < len(text):
        bounds.append((start, len(text)))
    return bounds


def _clause_of(clauses: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    for lo, hi in clauses:
        if lo <= pos < hi:
            return lo, hi
    return pos, pos


def _unit_after(text: str, end: int, clause_hi: int) -> tuple[str | None, int]:
    """Unit word immediately following a number (only whitespace between)."""
    tail = text[end:clause_hi]
    stripped = tail.lstrip()
    offset = end + (len(tail) - len(stripped))
    best: tuple[str | None, int] = (None, end)
    best_len = 0
    for unit, words in UNIT_WORDS.items():
        for w in words:
            if not stripped.startswith(w):
                continue
            after = stripped[len(w):len(w) + 1]
            if _is_latin_word(w) and w[-1].isalnum() and after and (after.isalnum()):
                continue
            if len(w) > best_len:
                best, best_len = (unit, offset + len(w)), len(w)
    # "102 degrees f" / "39 degree c"
    if best[0] == "degree":
        rest = text[best[1]:clause_hi]
        rs = rest.lstrip()
        for unit in ("f", "c"):
            for w in UNIT_WORDS[unit]:
                if rs.startswith(w) and not (w[-1].isalnum() and rs[len(w):len(w) + 1].isalnum()):
                    return unit, best[1] + (len(rest) - len(rs)) + len(w)
    return best


def _nearest_keyword(text: str, num: _Num, lo: int, hi: int, nums: list[_Num]) -> str | None:
    """The field whose keyword is closest to the number with no other number between them.
    Preceding keywords win; a following keyword is used only if nothing precedes. Ties → None."""
    others = [n for n in nums if n is not num and lo <= n.start < hi]
    before: list[tuple[int, str]] = []
    after: list[tuple[int, str]] = []
    for fld, words in KEYWORDS.items():
        if fld == "pregnancy":
            continue
        for s, e in _find_terms(text, words, lo, hi):
            if e <= num.start and not any(e <= o.start and o.end <= num.start for o in others):
                before.append((num.start - e, fld))
            elif s >= num.end and not any(num.end <= o.start and o.end <= s for o in others):
                after.append((s - num.end, fld))
    for group in (before, after):
        if group:
            group.sort()
            best = group[0][0]
            fields = {f for d, f in group if d == best}
            return fields.pop() if len(fields) == 1 else None
    return None


def _in_range(key: str, value: float) -> bool:
    lo, hi = BOUNDS[key]
    return lo <= value <= hi


def _as_int(c: Candidate, key: str, value: float) -> int | None:
    if value != int(value):
        c.add("non_integer")
        return None
    if not _in_range(key, value):
        c.add("out_of_domain_range")
        return None
    return int(value)


# ── Field builders ───────────────────────────────────────────────────────────────────────────────


def _temperature(num: _Num, unit: str | None, end: int) -> Candidate:
    c = Candidate("temp", num.start, end, num.value)
    v = num.value
    if unit == "f":
        c.unit, c.unit_source = "f", "spoken"
    elif unit == "c":
        c.unit, c.unit_source = "c", "spoken"
    elif BOUNDS["temp_c"][0] <= v <= BOUNDS["temp_c"][1]:
        c.unit, c.unit_source = "c", "inferred"
        c.add("unit_inferred")
    elif F_RANGE[0] <= v <= F_RANGE[1]:
        c.unit, c.unit_source = "f", "inferred"
        c.add("unit_inferred")
    else:
        c.add("unit_unknown")
    if num.has_decimal or num.digits >= 4:
        c.add("decimal_ambiguity")
    if c.unit is not None:
        temp_c = (v - 32.0) * 5.0 / 9.0 if c.unit == "f" else v  # exact; never rounded
        if _in_range("temp_c", temp_c):
            c.normalized = {"temp_c": temp_c}
        else:
            c.add("out_of_domain_range")
    return c


def extract(transcript: str) -> list[Candidate]:
    text = _fold(transcript)
    clauses = _clauses(text)
    nums = _numbers(text)
    used: set[int] = set()
    out: list[Candidate] = []

    # Blood pressure pairs first: "120/80", "120 by 80", "120 over 80".
    for i, a in enumerate(nums):
        if i in used or i + 1 >= len(nums):
            continue
        b = nums[i + 1]
        between = text[a.end:b.start]
        if re.fullmatch(r"\s*(?:/|by|over|बटा|बाई|ବାଇ|ବଟା)\s*", between):
            c = Candidate("bp", a.start, b.end, a.value, b.value, "mmhg")
            sbp = _as_int(c, "sbp", a.value)
            dbp = _as_int(c, "dbp", b.value)
            if sbp is not None and dbp is not None:
                if dbp >= sbp:
                    c.add("bp_order_invalid")
                else:
                    c.normalized = {"sbp": sbp, "dbp": dbp}
            used.update({i, i + 1})
            out.append(c)

    for i, num in enumerate(nums):
        if i in used:
            continue
        lo, hi = _clause_of(clauses, num.start)
        unit, unit_end = _unit_after(text, num.end, hi)
        end = unit_end if unit else num.end

        if unit in ("f", "c", "degree"):
            out.append(_temperature(num, None if unit == "degree" else unit, end))
            continue
        if unit == "percent":
            c = Candidate("spo2", num.start, end, num.value, unit="percent")
            v = _as_int(c, "spo2", num.value)
            c.normalized = {"spo2": v} if v is not None else None
            out.append(c)
            continue
        if unit in ("days", "weeks", "hours"):
            out.append(Candidate("symptom_duration", num.start, end, num.value, unit=unit))  # display only
            continue
        if unit in ("years", "months"):
            is_age = bool(_find_terms(text, KEYWORDS["age"], lo, hi)) or bool(_find_terms(text, AGE_OLD_MARKERS, lo, hi))
            if is_age:
                c = Candidate("age", num.start, end, num.value, unit=unit)
                if unit == "months":
                    c.add("age_unit_months")  # never prefilled as years
                else:
                    v = _as_int(c, "age_years", num.value)
                    c.normalized = {"age_years": v} if v is not None else None
                out.append(c)
            elif unit == "months" and not _find_terms(text, KEYWORDS["pregnancy"], lo, hi):
                out.append(Candidate("symptom_duration", num.start, end, num.value, unit=unit))
            else:
                c = Candidate("unassigned", num.start, end, num.value, unit=unit)
                c.add("needs_assignment")
                out.append(c)
            continue

        fld = _nearest_keyword(text, num, lo, hi, nums)
        if fld == "temp":
            out.append(_temperature(num, None, end))
        elif fld in ("spo2", "pulse", "resp_rate"):
            c = Candidate(fld, num.start, end, num.value, unit="percent" if fld == "spo2" else "per_min")
            v = _as_int(c, fld, num.value)
            c.normalized = {fld: v} if v is not None else None
            out.append(c)
        elif fld == "age":
            c = Candidate("age", num.start, end, num.value, unit="years")
            c.add("unit_inferred")
            v = _as_int(c, "age_years", num.value)
            c.normalized = {"age_years": v} if v is not None else None
            out.append(c)
        else:  # bp keyword with a single number, no keyword, or ambiguous keywords
            c = Candidate("unassigned", num.start, end, num.value, unit=unit if unit == "per_min" else None)  # type: ignore[arg-type]
            c.add("needs_assignment")
            out.append(c)

    # Pregnancy statements (no number needed).
    for s, e in _find_terms(text, KEYWORDS["pregnancy"]):
        if any(c.field == "pregnancy" and c.char_start <= s < c.char_end for c in out):
            continue
        c = Candidate("pregnancy", s, e, None, normalized={"pregnant": True})
        out.append(c)

    for c in out:
        if any(n.words and c.char_start <= n.start < c.char_end for n in nums):
            c.add("number_words")
        if c.field == "pregnancy":
            continue
        before = re.findall(r"[\w\u0900-\u097F\u0B00-\u0B7F]+", text[max(0, c.char_start - 16):c.char_start])
        after = re.findall(r"[\w\u0900-\u097F\u0B00-\u0B7F]+", text[c.char_end:c.char_end + 16])
        # An unrecognised word containing ଶହ/सौ next to a number (e.g. ତିନିଶହ misspelt): the number is incomplete.
        if (before and any(h in before[-1] for h in _HUNDRED_SUFFIXES) and _joined_hundred(before[-1]) is None and before[-1] not in _INDIC_HUNDRED) or (
            after and any(h in after[0] for h in _HUNDRED_SUFFIXES) and _joined_hundred(after[0]) is None and after[0] not in _INDIC_HUNDRED
        ):
            c.add("number_modifier_unparsed")
            c.normalized = None
        # A number word that is also an ordinary word (ବାର "times"), or a number used as a count (एक बार
        # "once", ଦୁଇ ଥର "twice"): may not be a measurement at all.
        in_span = _INDIC_TOKEN.findall(text[c.char_start:c.char_end])
        span_words = text[c.char_start:c.char_end].split()
        lone_one = c.raw_value == 1 and c.field not in ("bp", "symptom_duration") and span_words[:1] and span_words[0] in _LONE_ONE_WORDS
        if any(w in _HOMOGRAPH_NUMBER_WORDS for w in in_span) or (after and after[0] in _COUNT_WORDS) or lone_one:
            c.add("number_word_homograph")
            c.normalized = None
        # A unit word that starts like "degree" but is not recognised (ASR garble, e.g. डिग्रलियस for
        # "degree Celsius"): the unit is unclear, so it must not be inferred.
        if c.field == "temp" and after and after[0].startswith(("डिग्र", "ଡିଗ୍ର", "degr")) and c.unit_source == "inferred":
            c.add("unit_unclear")
        half = re.match(r"\s*(?:and\s+(?:a\s+)?half|half)" + _RB, text[c.char_end:])
        # A decimal that was not parsed together with the number ("39 point 5", "39 दशमलव 5", "39. 5"):
        # the number heard is only the integer part, which can sit just below a threshold (39 vs 39.5).
        split_decimal = _SPLIT_DECIMAL.match(text, c.char_end)
        if split_decimal and c.field != "pregnancy":
            c.add("number_modifier_unparsed")
            c.normalized = None
            c.char_end = split_decimal.end()
        if (before and before[-1] in _NUMBER_MODIFIERS) or (after and after[0] in _NUMBER_MODIFIERS) or half:
            c.add("number_modifier_unparsed")
            c.normalized = None
            # Widen the evidence span so the reviewer sees the modifier word itself (साढ़े उनतालीस …).
            if before and before[-1] in _NUMBER_MODIFIERS:
                c.char_start = text.rindex(before[-1], max(0, c.char_start - 16), c.char_start)
            if half:
                c.char_end += half.end()
            elif after and after[0] in _NUMBER_MODIFIERS:
                c.char_end = text.index(after[0], c.char_end) + len(after[0])
    # Two numbers separated only by spaces (not a BP pattern) are probably one number spoken in parts
    # ("one twenty", "nine eight") or two readings: never merged, never confirmable as heard.
    spans = sorted((c.char_start, c.char_end, c) for c in out if c.field != "pregnancy" and c.raw_value is not None)
    for (s1, e1, c1), (s2, e2, c2) in zip(spans, spans[1:]):
        if text[e1:s2].strip() == "" and c1.field != "bp" and c2.field != "bp":
            for c in (c1, c2):
                c.add("number_sequence_ambiguous")
                c.normalized = None
    _apply_context_gate(text, clauses, out, nums)
    _apply_context_flags(text, clauses, out)
    for c in out:
        if c.field == "pregnancy" and ("uncertainty" in c.flags or "negation" in c.flags):
            # "not sure if pregnant", "pregnancy test not done", "not pregnant": a clause-level cue cannot
            # tell these apart, so no value is proposed — the reviewer must choose explicitly (Correct).
            c.normalized = None
    return sorted(out, key=lambda c: c.char_start)


# ── Clean-context gate (allowlist) ───────────────────────────────────────────────────────────────
# One-click confirmation is allowed only for the plain shape "<field word> [filler] <number> [unit] [filler]".
# Any other word next to the value ("below 90", "90 से कम", "15 in 30 seconds", "90 to 95", "ऑक्सीजन लगा दो",
# "her child is 3") makes it `context_unclear`: the reviewer enters it. This is an allowlist because a list
# of bad words kept missing new phrasings (final council, 2026-10-01). It is still a word list: it reduces,
# and does not remove, the chance that a wrong value can be confirmed with one click.
_FILLER_BEFORE = {"is", "of", "rate", "level", "reading", "count", "at", "was", "are", ":", "=",
                  "है", "हैं", "का", "की", "के", "दर", "स्तर", "था", "थी", "hai", "ka", "ki", "ke",
                  "ଅଛି", "ହେଉଛି", "ହେଲା", "ର", "ମାତ୍ରା", "ସ୍ତର", "ଥିଲା"}
_FILLER_AFTER = {"है", "हैं", "था", "थी", "hai", "ଅଛି", "ହେଉଛି", "ଥିଲା", "now", "अभी", "ଏବେ", "only"}
_SEP = re.compile(r"[\s:=,]+")


def _clean_context(text: str, c: Candidate, lo: int, hi: int) -> bool:
    nxt = text[c.char_end:c.char_end + 1]
    if nxt and (nxt.isalnum() or nxt == "-" or 0x0900 <= ord(nxt) <= 0x0B7F):  # 90ରୁ, 110-120
        return False
    keywords = [w for fld, ws in KEYWORDS.items() if fld != "pregnancy" for w in ws]
    before_kw = [e for s_, e in _find_terms(text, tuple(keywords), lo, c.char_start) if e <= c.char_start]
    tail = re.findall(rf"[{_WORDCH}%/°]+|[:=]", text[c.char_end:hi])
    if before_kw:
        between = [w for w in _SEP.split(text[max(before_kw):c.char_start]) if w]
        if any(w not in _FILLER_BEFORE for w in between):
            return False
    else:
        # No field word before the value: allowed only as "<number> <unit> <field word>" (102 डिग्री बुखार).
        if not tail or not any(tail[0].startswith(k) or k.startswith(tail[0]) for k in keywords):
            return False
        tail = tail[1:]
    if tail and tail[0] not in _FILLER_AFTER and not any(tail[0] == k or tail[0].startswith(k) for k in keywords):
        return False
    return True


def _apply_context_gate(text: str, clauses: list[tuple[int, int]], out: list[Candidate], nums: list[_Num]) -> None:
    for c in out:
        if c.field in ("pregnancy", "symptom_duration", "unassigned"):
            continue
        lo, hi = _clause_of(clauses, c.char_start)
        if not _clean_context(text, c, lo, hi):
            c.add("context_unclear")
            c.normalized = None
        if c.field == "bp":
            # "BP one sixty over one ten" parses as 60/1 with the "one"s left over: a number right next to the
            # pair means it was split. Shorthand such as "13 by 9" (for 130/90) is flagged, never multiplied.
            if any(n.end <= c.char_start and not text[n.end:c.char_start].strip() for n in nums) or any(
                n.start >= c.char_end and not text[c.char_end:n.start].strip() for n in nums
            ):
                c.add("number_sequence_ambiguous")
                c.normalized = None
            if c.raw_value is not None and c.raw_value2 is not None and c.raw_value < 30 and c.raw_value2 < 30:
                c.add("bp_shorthand_possible")
                c.normalized = None


def _apply_context_flags(text: str, clauses: list[tuple[int, int]], out: list[Candidate]) -> None:
    for c in out:
        lo, hi = _clause_of(clauses, c.char_start)
        if _find_terms(text, NEGATION, lo, hi):
            c.add("negation")
        if _find_terms(text, UNCERTAINTY, lo, hi):
            c.add("uncertainty")
        if _find_terms(text, TEMPORAL, lo, hi):
            c.add("temporal_reference")
        if c.field == "spo2" and _find_terms(text, OXYGEN_CONTEXT, lo, hi):
            c.add("oxygen_context")
    counts: dict[str, int] = {}
    for c in out:
        if c.field != "unassigned":
            counts[c.field] = counts.get(c.field, 0) + 1
    for c in out:
        if counts.get(c.field, 0) > 1:
            c.add("multiple_values")
