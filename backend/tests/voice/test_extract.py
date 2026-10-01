"""Deterministic critical-value extractor (docs/12 §5). Synthetic phrases only; these are test
scenarios for transcription errors, not clinical thresholds."""

import pytest

from app.rules.models import TriageInput, Vitals
from app.voice.extract import BOUNDS, F_RANGE, extract


def only(text: str, field: str):
    found = [c for c in extract(text) if c.field == field]
    assert len(found) == 1, (text, [(c.field, c.flags) for c in extract(text)])
    return found[0]


def span(text: str, c) -> str:
    return text[c.char_start:c.char_end]


# ── Temperature: unrounded conversion and the ATP >39 °C boundary ────────────────────────────────


def test_fahrenheit_converted_exactly_not_rounded():
    c = only("temperature 102 degrees fahrenheit", "temp")
    assert c.unit == "f"
    assert c.normalized == {"temp_c": (102 - 32) * 5 / 9}
    assert c.normalized["temp_c"] != 38.9


@pytest.mark.parametrize(
    "f, above_39",
    [("102.2", False), ("102.3", True)],  # 102.2 °F == 39.0 °C exactly (not >39); 102.3 °F is >39
)
def test_boundary_pairs_survive_conversion(f, above_39):
    c = only(f"temp {f} F", "temp")
    assert (c.normalized["temp_c"] > 39) is above_39


def test_102_vs_100_point_2_are_different_values_and_decimal_is_flagged():
    a = only("temperature 102", "temp")
    b = only("temperature 100.2", "temp")
    assert a.normalized["temp_c"] != b.normalized["temp_c"]
    assert "decimal_ambiguity" in b.flags
    assert "decimal_ambiguity" not in a.flags


def test_unit_inference_uses_only_engine_domain():
    assert only("temp 38.5", "temp").unit == "c"
    assert "unit_inferred" in only("temp 38.5", "temp").flags
    assert only("temp 101", "temp").unit == "f"
    for value in ("60", "1002", "20"):
        c = only(f"temp {value}", "temp")
        assert "unit_unknown" in c.flags and c.normalized is None


def test_explicit_unit_is_not_inferred():
    c = only("temp 39 degree celsius", "temp")
    assert c.unit == "c" and "unit_inferred" not in c.flags


def test_f_range_is_exact_image_of_engine_celsius_domain():
    lo, hi = BOUNDS["temp_c"]
    assert F_RANGE == (lo * 9 / 5 + 32, hi * 9 / 5 + 32)


# ── Other vitals ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, value", [("oxygen is 92 percent", 92), ("oxygen is ninety eight percent", 98), ("spo2 95%", 95)])
def test_spo2(text, value):
    assert only(text, "spo2").normalized == {"spo2": value}


@pytest.mark.parametrize("text, value", [("pulse is one hundred and twenty", 120), ("pulse is eighty", 80), ("heart rate 96", 96)])
def test_pulse_words_and_digits(text, value):
    assert only(text, "pulse").normalized == {"pulse": value}


def test_spo2_oxygen_context_flagged_never_inferred():
    c = only("oxygen 94 percent on oxygen support", "spo2")
    assert "oxygen_context" in c.flags
    assert "on_supplemental_oxygen" not in (c.normalized or {})


@pytest.mark.parametrize("text", ["BP 120/80", "bp 120 by 80", "blood pressure 120 over 80"])
def test_bp_patterns(text):
    assert only(text, "bp").normalized == {"sbp": 120, "dbp": 80}


def test_bp_reversed_order_not_prefilled():
    c = only("bp 80/120", "bp")
    assert "bp_order_invalid" in c.flags and c.normalized is None


def test_out_of_engine_range_is_flagged_not_prefilled():
    c = only("breathing 88", "resp_rate")
    assert "out_of_domain_range" in c.flags and c.normalized is None


def test_adjacent_keywords_bind_to_nearest_preceding_number():
    cands = {c.field: c for c in extract("pulse 20 breathing 22")}
    assert cands["pulse"].raw_value == 20 and cands["resp_rate"].raw_value == 22


# ── Duration, age, pregnancy ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, value", [("fever for three days", 3), ("fever for thirteen days", 13), ("bukhar 3 din se", 3)])
def test_duration_three_vs_thirteen(text, value):
    c = only(text, "symptom_duration")
    assert c.raw_value == value and c.unit == "days"
    assert c.normalized is None  # display only; not an engine input


def test_age_years_and_months():
    assert only("age 45 years", "age").normalized == {"age_years": 45}
    months = only("baby is 8 months old", "age")
    assert "age_unit_months" in months.flags and months.normalized is None


@pytest.mark.parametrize("text", ["she is not pregnant", "not sure if she is pregnant", "pregnancy test not done", "maybe pregnant"])
def test_pregnancy_with_negation_or_doubt_proposes_no_value(text):
    """Final council: clause-level cues cannot tell 'not pregnant' from 'test not done' or doubt.
    No value is proposed; the reviewer must choose explicitly."""
    c = only(text, "pregnancy")
    assert c.normalized is None


def test_plain_pregnancy_statement():
    assert only("she is pregnant", "pregnancy").normalized == {"pregnant": True}


# ── Negation, uncertainty, temporal, multiple values ─────────────────────────────────────────────


def test_negation_scope_is_the_clause():
    cands = extract("I do not have chest pain, pulse is 80")
    assert all("negation" not in c.flags for c in cands)
    assert "negation" in only("fever is not 102", "temp").flags


def test_postpositional_hindi_and_odia_negation():
    assert "negation" in only("बुखार 102 नहीं है", "temp").flags
    assert "negation" in only("ଜ୍ୱର 102 ନାହିଁ", "temp").flags


def test_short_indic_negators_do_not_match_inside_words():
    # "परेशानी" contains पर; "नब्ज" starts with न — neither is a clause split nor a negation.
    assert only("बुखार 102 है परेशानी", "temp").flags == ["unit_inferred"]


def test_uncertainty_and_temporal_flags():
    assert "uncertainty" in only("temperature maybe 101", "temp").flags
    assert "temporal_reference" in only("yesterday temperature was 104", "temp").flags


def test_multiple_values_for_one_field_are_all_flagged():
    cands = [c for c in extract("temperature 101. temperature 103") if c.field == "temp"]
    assert len(cands) == 2 and all("multiple_values" in c.flags for c in cands)


def test_bare_number_is_unassigned():
    c = only("it is 98", "unassigned")
    assert "needs_assignment" in c.flags and c.normalized is None


# ── Scripts and offsets ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["ତାପମାନ ୧୦୨ ଡିଗ୍ରୀ", "तापमान १०२ डिग्री", "temperature 102 degrees"])
def test_odia_and_devanagari_digits(text):
    assert only(text, "temp").raw_value == 102


def test_offsets_index_the_raw_transcript():
    text = "ତିନି ଦିନ ହେଲା ଜ୍ୱର ହେଉଛି, ତାପମାନ ୧୦୨ ଡିଗ୍ରୀ"
    c = only(text, "temp")
    assert span(text, c) == "୧୦୨ ଡିଗ୍ରୀ"


@pytest.mark.parametrize(
    "text, field, value",
    [
        ("तापमान एक सौ दो डिग्री है", "temp", 102),  # real IndicConformer output style (docs/12 §9.2)
        ("ତାପମାନ ଏକ ଶହ ଦୁଇ ଡିଗ୍ରୀ", "temp", 102),
        ("मुझे तीन दिन से बुखार है", "symptom_duration", 3),
        ("ତିନି ଦିନ ହେଲା ଜ୍ୱର", "symptom_duration", 3),
        ("नब्ज़ बानवे है", "pulse", 92),
        ("ऑक्सीजन छियानवे प्रतिशत", "spo2", 96),
        ("तापमान सौ दशमलव दो", "temp", 100.2),
    ],
)
def test_hindi_odia_number_words_from_closed_table(text, field, value):
    c = only(text, field)
    assert c.raw_value == value and "number_words" in c.flags


def test_hindi_bp_in_words():
    assert only("बीपी एक सौ बीस बटा अस्सी", "bp").normalized == {"sbp": 120, "dbp": 80}


def test_hindi_table_spot_checks():
    from app.voice.extract import INDIC_NUMBER_WORDS, _HI_WORDS

    assert len(_HI_WORDS) == 100
    assert [INDIC_NUMBER_WORDS[w] for w in ("उन्नीस", "उनतालीस", "अड़तीस", "अट्ठानवे", "निन्यानवे", "ଊଣେଇଶି", "ନବେ")] == [19, 39, 38, 98, 99, 19, 90]


def test_number_words_outside_the_table_are_never_guessed():
    """Odia compounds 21–99 are not in the draft table: no value is invented (stays for the ANM)."""
    assert [c for c in extract("ନାଡି ଏକୋଇଶି") if c.field == "pulse"] == []


def test_empty_and_numberless_transcripts():
    assert extract("") == []
    assert extract("headache and body pain") == []


# ── Contract with the rules engine ───────────────────────────────────────────────────────────────


def test_bounds_match_engine_models():
    for key in ("spo2", "pulse", "resp_rate", "sbp", "dbp", "temp_c"):
        meta = {type(m).__name__: m for m in Vitals.model_fields[key].metadata}
        assert (meta["Ge"].ge, meta["Le"].le) == BOUNDS[key], key
    age = {type(m).__name__: m for m in TriageInput.model_fields["age_years"].metadata}
    assert (age["Ge"].ge, age["Le"].le) == BOUNDS["age_years"]


def test_normalized_values_are_accepted_by_engine_vitals():
    for text in ("temperature 102 F", "oxygen 92 percent", "pulse 120", "breathing 22", "bp 120/80"):
        for c in extract(text):
            if c.normalized:
                Vitals(**c.normalized)  # strict model: raises if a type or range is wrong


# ── Final council regressions: no plausible wrong value without a flag ───────────────────────────

from app.voice.readback import can_confirm  # noqa: E402


def _confirmable(text):
    return [(c.field, c.raw_value) for c in extract(text) if can_confirm(c.field, c.normalized, c.flags) and c.field != "symptom_duration"]


@pytest.mark.parametrize("text", ["बुखार साढ़े उनतालीस डिग्री", "सवा उनतालीस डिग्री", "पौने उनतालीस डिग्री", "temperature thirty nine and a half", "ଜ୍ୱର ସାଢ଼େ ଉଣେଇଶି"])
def test_fraction_modifiers_are_not_dropped_silently(text):
    """साढ़े उनतालीस = 39.5 must never become a confirmable 39.0 (ATP >39 °C boundary)."""
    assert _confirmable(text) == []
    temps = [c for c in extract(text) if c.field == "temp"]
    assert all(c.normalized is None for c in temps)


@pytest.mark.parametrize("text", ["pulse one twenty", "pulse one ten", "spo2 nine eight", "spo2 nine four", "SpO2 is 9 4", "ଅମ୍ଳଜାନ ଅଶୀ ପାଞ୍ଚ", "नब्ज़ अस्सी पांच", "bp one twenty over eighty"])
def test_numbers_spoken_in_parts_are_never_summed_or_confirmable(text):
    assert _confirmable(text) == []


def test_keyword_digits_are_not_numbers():
    assert [c.field for c in extract("spo2 95")] == ["spo2"]
    assert [c.field for c in extract("sp02 is 97 percent")] == ["spo2"]


@pytest.mark.parametrize("text", ["oxygen was 95 now 85", "ऑक्सीजन पचानवे था", "yesterday fever 104"])
def test_past_values_are_not_confirmable_as_heard(text):
    assert _confirmable(text) == []


def test_legal_english_compounds_still_parse():
    assert only("pulse is one hundred and twenty", "pulse").normalized == {"pulse": 120}
    assert only("oxygen ninety two percent", "spo2").normalized == {"spo2": 92}
    assert only("temperature one hundred two", "temp").raw_value == 102


def test_blocking_flags_prevent_one_click_confirm():
    for flag in ("negation", "uncertainty", "temporal_reference", "multiple_values", "number_modifier_unparsed", "number_sequence_ambiguous"):
        assert can_confirm("temp", {"temp_c": 38.0}, [flag]) is False
    assert can_confirm("temp", {"temp_c": 38.0}, ["unit_inferred", "number_words", "decimal_ambiguity"]) is True


def test_modifier_is_inside_the_evidence_span_and_read_back_quotes_the_words():
    from app.voice.readback import readback_text

    text = "तापमान साढ़े उनतालीस डिग्री है"
    c = only(text, "temp")
    assert span(text, c) == "साढ़े उनतालीस डिग्री"
    said = readback_text(c.field, c.raw_value, c.raw_value2, c.unit, c.normalized, "hi", heard=span(text, c), flags=c.flags)
    assert "“साढ़े उनतालीस डिग्री”" in said and "39 °C" not in said


# ── Regressions from live on-device recordings (2026-10-01; transcripts copied from the real model) ─


@pytest.mark.parametrize("text, field, value", [("ନାଡି ଏକଶହ କୋଡ଼ିଏ", "pulse", 120), ("ତାପମାନ ଏକଶହ ଦୁଇ ଡିଗ୍ରୀ", "temp", 102), ("नब्ज़ दोसौ", "pulse", 200)])
def test_joined_hundred_words(text, field, value):
    assert only(text, field).raw_value == value


def test_joined_hundreds_from_live_odia_recording():
    cands = extract("ଦୁଇଶହ ଦୁଇ ଦୁଇଶହ ଦୁଇ ଦୁଇଶହ ଦୁଇ")
    assert [c.raw_value for c in cands] == [202, 202, 202]  # never a silent 2


def test_unknown_hundred_word_blocks_instead_of_dropping():
    c = only("ନାଡି ତିନଶହ କୋଡ଼ିଏ", "pulse")  # misspelt joined form: must not become a clean pulse 20
    assert c.normalized is None and not can_confirm(c.field, c.normalized, c.flags)


def test_garbled_unit_word_blocks_unit_inference():
    c = only("मुझे तेज बुखार है एक सौ आठ डिग्रलियस", "temp")  # live transcript: garbled "degree Celsius"
    assert "unit_unclear" in c.flags and not can_confirm(c.field, c.normalized, c.flags)
    assert can_confirm(*(lambda x: (x.field, x.normalized, x.flags))(only("बुखार एक सौ दो डिग्री", "temp")))


def test_english_spoken_with_hindi_selected_yields_no_values():
    """Live: English speech with Hindi selected is written phonetically; nothing is guessed from it."""
    live = "आई हैव हैड फीवर फॉर थ्री डेज मई टेम्परेचर इज वन हंड्रेड एंड टू डिग्रीज फेरनहााइट"
    assert [c for c in extract(live) if c.normalized] == []


# ── Odia 21–99 (Unicode CLDR spellings), leading ଶହେ, Hindi alternate spellings, homographs ─────────
# Behaviour on triage-style sentences, not a check of the table against itself. The spellings come from
# a written standard (CLDR), not from real speech: what an ASR model writes may differ (docs/12 §5).


@pytest.mark.parametrize(
    "text, field, normalized",
    [
        ("ଅକ୍ସିଜେନ ଚଉରାନବେ ପ୍ରତିଶତ", "spo2", {"spo2": 94}),
        ("ନାଡ଼ି ଅଠାଶୀ", "pulse", {"pulse": 88}),
        ("ଶ୍ୱାସ ବାଇଶ", "resp_rate", {"resp_rate": 22}),
        ("ବୟସ ପଞ୍ଚତିରିଶ ବର୍ଷ", "age", {"age_years": 35}),
        ("ତାପମାନ ଅଠତିରିଶ ଦଶମିକ ପାଞ୍ଚ ଡିଗ୍ରୀ", "temp", {"temp_c": 38.5}),
        ("ଜ୍ୱର ଶହେ ଦୁଇ ଡିଗ୍ରୀ", "temp", {"temp_c": (102 - 32) * 5 / 9}),
        ("ନାଡ଼ି ଶହେ କୋଡ଼ିଏ", "pulse", {"pulse": 120}),
        ("ନାଡ଼ି ଏକ ଶହ ଦୁଇ", "pulse", {"pulse": 102}),
        ("ଅକ୍ସିଜେନ ନିଆଁନବେ ପ୍ରତିଶତ", "spo2", {"spo2": 99}),
        ("ऑक्सीजन चौरानबे प्रतिशत", "spo2", {"spo2": 94}),
        ("नब्ज़ उनासी", "pulse", {"pulse": 79}),
        ("ऑक्सीजन चौरानवे प्रतिशत", "spo2", {"spo2": 94}),  # the existing spelling still works
    ],
)
def test_odia_cldr_and_hindi_variant_numbers_in_sentences(text, field, normalized):
    c = only(text, field)
    assert c.normalized == normalized and "number_words" in c.flags


def test_odia_bp_with_leading_hundred():
    c = only("ରକ୍ତଚାପ ଶହେ କୋଡ଼ିଏ ବାଇ ଅଶୀ", "bp")
    assert c.normalized == {"sbp": 120, "dbp": 80}


def test_fever_boundary_unchanged_for_odia_words():
    # 102.2 °F = 39.0 °C exactly (not >39); the word path uses the same exact conversion.
    c = only("ଜ୍ୱର ଶହେ ଦୁଇ ଦଶମିକ ଦୁଇ ଡିଗ୍ରୀ", "temp")
    assert c.raw_value == 102.2 and c.unit == "f" and not c.normalized["temp_c"] > 39


@pytest.mark.parametrize(
    "text",
    [
        "ଶ୍ୱାସ ଅନେକ ବାର ନେଉଛି",  # "breathing many times": ବାର is "times", not 12
        "ଶ୍ୱାସ ବାର",  # even a lone ବାର could be "time(s)"
        "ନାଡ଼ି ଏକ ଶହ ବାର",  # 112 or "a hundred times"
        "सांस एक बार",  # "breath once"
        "ନାଡ଼ି ଦୁଇ ଥର",  # "twice"
        "pulse two times",
    ],
)
def test_homographs_and_counts_are_never_one_click_confirmable(text):
    from app.voice.readback import can_confirm

    cands = extract(text)
    assert cands and all(not can_confirm(c.field, c.normalized, c.flags) for c in cands)
    assert any("number_word_homograph" in c.flags for c in cands)


@pytest.mark.parametrize("text", ["ନାଡ଼ି ଦୁଇ ଶହେ", "ନାଡ଼ି ଏକ ଶହେ ଦୁଇ"])
def test_multiplier_before_leading_hundred_is_blocked_not_200(text):
    from app.voice.readback import can_confirm

    cands = extract(text)
    assert all(c.raw_value not in (200, 102, 100) or not can_confirm(c.field, c.normalized, c.flags) for c in cands)
    assert not any(can_confirm(c.field, c.normalized, c.flags) for c in cands)


@pytest.mark.parametrize(
    "text",
    [
        "ନାଡ଼ି ଦୁଇଶହେ",  # joined ଶହେ form: not in the table
        "ନାଡ଼ି ଅଠାସୀ",  # misspelling of ଅଠାଶୀ: never guessed
        "ନାଡ଼ି ଶହେରୁ",  # inflected
    ],
)
def test_unknown_odia_spellings_give_no_value(text):
    assert [c for c in extract(text) if c.normalized] == []


def test_tens_and_units_in_parts_still_ambiguous():
    for c in extract("ଅକ୍ସିଜେନ ନବେ ଚାରି ପ୍ରତିଶତ"):
        assert "number_sequence_ambiguous" in c.flags and c.normalized is None


def test_out_of_range_odia_spo2_is_flagged():
    c = only("ଅକ୍ସିଜେନ ଶହେ ଦଶ ପ୍ରତିଶତ", "spo2")
    assert "out_of_domain_range" in c.flags and c.normalized is None


def test_negation_and_uncertainty_still_block_new_odia_words():
    from app.voice.readback import can_confirm

    for text in ("ଜ୍ୱର ଅଠତିରିଶ ଡିଗ୍ରୀ ନାହିଁ", "ନାଡ଼ି ପ୍ରାୟ ଅଠାଶୀ"):
        c = [c for c in extract(text) if c.field in ("temp", "pulse")][0]
        assert not can_confirm(c.field, c.normalized, c.flags)


def test_number_words_do_not_collide_with_other_lexicons():
    from app.voice.extract import INDIC_NUMBER_WORDS, KEYWORDS, NEGATION, TEMPORAL, UNCERTAINTY, UNIT_WORDS

    other = {w for ws in KEYWORDS.values() for w in ws} | {w for ws in UNIT_WORDS.values() for w in ws}
    other |= set(NEGATION) | set(UNCERTAINTY) | set(TEMPORAL)
    assert not other.intersection(INDIC_NUMBER_WORDS)


@pytest.mark.parametrize("text", ["ନାଡ଼ି ଏକ ଟିକେ ବେଶୀ", "नब्ज़ एक दम तेज़", "pulse is one of concern", "ବୟସ ଏକ ବର୍ଷ"])
def test_lone_one_word_may_be_the_article_a(text):
    from app.voice.readback import can_confirm

    c = [c for c in extract(text) if c.field in ("pulse", "age")][0]
    assert "number_word_homograph" in c.flags and not can_confirm(c.field, c.normalized, c.flags)


def test_one_inside_a_larger_number_or_as_digit_is_unaffected():
    assert only("ଜ୍ୱର ଏକ ଶହ ଦୁଇ ଡିଗ୍ରୀ", "temp").normalized == {"temp_c": (102 - 32) * 5 / 9}
    assert only("pulse one hundred", "pulse").normalized == {"pulse": 100}
    assert "number_word_homograph" not in only("pulse 1", "pulse").flags


# ── Final council 2026-10-01: clean-context gate (allowlist) and split decimals ──────────────────────
# Phrasings that produced a wrong value confirmable with one click before the gate. They must stay blocked.
LEAKS = [
    "spo2 below 90", "ऑक्सीजन 90 से कम है", "ଅକ୍ସିଜେନ ୯୦ରୁ କମ", "spo2 92 ish", "breathing 15 in 30 seconds",
    "pulse 110-120", "spo2 90 to 95", "BP one sixty over one ten", "BP 13 by 9", "ऑक्सीजन लगा दो",
    "she ate 50% of her food", "पहला बच्चा 3 साल का है", "परसों बुखार 104", "nadi 1 minute mein 90",
    # split decimals: the integer part alone can sit just below the >39 °C boundary
    "temperature 39 point 5", "fever 102 point 4", "बुखार 39 दशमलव 5", "temperature 39 पॉइंट 5", "temp 39. 5",
    "temperature 39 and half",
    # oxygen support: the number alone would read as room air
    "oxygen 92 on oxygen mask",
]
# Plain phrasings that must stay one-click confirmable (demo sentences included), with the expected value.
CLEAN = [
    ("मुझे तीन दिन से बुखार है तापमान एक सौ दो डिग्री है", "temp", {"temp_c": (102 - 32) * 5 / 9}),
    ("pulse is one hundred and twenty", "pulse", {"pulse": 120}),
    ("oxygen ninety two percent", "spo2", {"spo2": 92}),
    ("My temperature is 102 degrees Fahrenheit. I do not have chest pain.", "temp", {"temp_c": (102 - 32) * 5 / 9}),
    ("BP 120/80", "bp", {"sbp": 120, "dbp": 80}),
    ("blood pressure 120 over 80", "bp", {"sbp": 120, "dbp": 80}),
    ("ବୟସ ପଞ୍ଚତିରିଶ ବର୍ଷ", "age", {"age_years": 35}),
    ("ଅକ୍ସିଜେନ ଚଉରାନବେ ପ୍ରତିଶତ", "spo2", {"spo2": 94}),
    ("तापमान 102 डिग्री नब्ज़ 120", "pulse", {"pulse": 120}),
    ("pulse rate 88", "pulse", {"pulse": 88}),
    ("मुझे 102 डिग्री बुखार है", "temp", {"temp_c": (102 - 32) * 5 / 9}),
    ("temperature 39.5", "temp", {"temp_c": 39.5}),
    ("तापमान उनतालीस दशमलव पांच", "temp", {"temp_c": 39.5}),
]


@pytest.mark.parametrize("text", LEAKS)
def test_known_leaks_are_never_one_click_confirmable(text):
    from app.voice.readback import can_confirm

    assert not any(can_confirm(c.field, c.normalized, c.flags) for c in extract(text) if c.field != "symptom_duration")


@pytest.mark.parametrize("text, field, normalized", CLEAN)
def test_plain_phrasings_stay_one_click_confirmable(text, field, normalized):
    from app.voice.readback import can_confirm

    c = only(text, field)
    assert c.normalized == normalized and can_confirm(c.field, c.normalized, c.flags), c.flags


def test_split_decimal_read_back_quotes_the_words():
    from app.voice.readback import readback_text

    text = "temperature 39 point 5"
    c = only(text, "temp")
    assert "number_modifier_unparsed" in c.flags and span(text, c) == "39 point 5"
    said = readback_text(c.field, c.raw_value, c.raw_value2, c.unit, c.normalized, "en", heard=span(text, c), flags=c.flags)
    assert "“39 point 5”" in said and "39 °C" not in said


def test_sentence_full_stop_is_not_a_decimal():
    from app.voice.readback import can_confirm

    for text in ("temperature 39. pulse 80", "pulse 80."):
        assert all(can_confirm(c.field, c.normalized, c.flags) for c in extract(text))


def test_bp_shorthand_and_split_pairs_are_flagged():
    assert "bp_shorthand_possible" in only("BP 13 by 9", "bp").flags
    assert "number_sequence_ambiguous" in only("BP one sixty over one ten", "bp").flags
