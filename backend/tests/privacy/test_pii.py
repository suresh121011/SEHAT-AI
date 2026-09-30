"""PII redaction: heuristic risk reduction, not anonymization (docs/11 §E, §F).

All identifiers below are synthetic (made-up digits and names). xfail tests document known false
negatives — they are NOT passes: name detection does not meet a deployment standard.
"""

import pytest

from app.privacy.normalize import has_unsupported_script, normalize
from app.privacy.pii import (
    PiiRedactionError,
    RedactedText,
    _Span,
    apply_redactions,
    merge_spans,
    redact,
)

CLINICAL = "BP 118/76, HR 110 bpm, RR 22/min, SpO2 97% on air, temp 38.9 C, fever for 3 days, severe abdominal pain, vomiting twice"


@pytest.fixture(scope="module", autouse=True)
def _warm_analyzer():
    redact("warm up")  # builds the cached analyzer once for this module


def test_clinical_values_preserved_exactly():
    assert redact(CLINICAL).text == CLINICAL


@pytest.mark.parametrize(
    "text,token,raw",
    [
        ("call +91 98765 43210 now", "[PHONE_REDACTED]", "98765"),
        ("call 09876543210 now", "[PHONE_REDACTED]", "9876543210"),
        ("call 9876543210 now", "[PHONE_REDACTED]", "9876543210"),
        ("call 98765-43210 now", "[PHONE_REDACTED]", "43210"),
        ("Aadhaar 2345 6789 0123 given", "[AADHAAR_REDACTED]", "6789"),
        ("Aadhaar 234567890123 given", "[AADHAAR_REDACTED]", "234567890123"),
        ("ABHA 12-3456-7890-1234 on card", "[ABHA_REDACTED]", "3456"),
        ("ABHA 12345678901234 on card", "[ABHA_REDACTED]", "12345678901234"),
        ("ABHA address test.user@sbx noted", "[ABHA_REDACTED]", "test.user"),
        ("PAN ABCDE1234F noted", "[PAN_REDACTED]", "ABCDE1234F"),
        ("mail a.b@example.com today", "[EMAIL_REDACTED]", "example.com"),
        ("DOB 12/03/1990 recorded", "[DATE_REDACTED]", "1990"),
        ("born 5 March 1988 in town", "[DATE_REDACTED]", "1988"),
        ("Patient Ramesh Kumar has fever", "[PERSON_REDACTED]", "Ramesh"),
        ("Smt. Sunita Devi has fever", "[PERSON_REDACTED]", "Sunita"),
        ("her name is Lakshmi Nayak", "[PERSON_REDACTED]", "Lakshmi"),
    ],
)
def test_identifiers_are_redacted(text, token, raw):
    out = redact(text)
    assert token in out.text
    assert raw not in out.text
    assert out.redacted_total >= 1


@pytest.mark.parametrize(
    "text,raw",
    [
        ("call ९८७६५४३२१० now", "९८७६५४३२१०"),  # Devanagari digits in English text
        ("call ୯୮୭୬୫୪୩୨୧୦ now", "୯୮୭୬୫୪୩୨୧୦"),  # Odia digits
        ("call ９８７６５４３２１０ now", "９８７６５４３２１０"),  # fullwidth digits
        ("call 98765​43210 now", "98765​43210"),  # zero-width space inside
        ("call 9́8765 43210 now", "9́8765"),  # combining mark between digits
    ],
)
def test_obfuscated_digits_are_normalized_then_redacted_or_blocked(text, raw):
    try:
        out = redact(text)
    except PiiRedactionError as exc:
        assert exc.reason_code == "residual_identifier_pattern"
        return
    assert raw not in out.text and "9876543210" not in out.text.replace(" ", "")


@pytest.mark.parametrize(
    "text",
    [
        "number is nine 8 seven 6 5 4 3 2",
        "number nau aath saat chhe paanch char teen do",
        "id 1.2.3.4.5.6.7.8.9",
    ],
)
def test_split_or_spelled_numbers_fail_closed(text):
    with pytest.raises(PiiRedactionError) as exc:
        redact(text)
    assert exc.value.reason_code == "residual_identifier_pattern"


def test_separator_split_number_is_redacted_or_blocked():
    try:
        out = redact("dial 98 - 76 - 54 - 32 - 10")
    except PiiRedactionError as exc:
        assert exc.reason_code == "residual_identifier_pattern"
        return
    assert "98 - 76" not in out.text and "32 - 10" not in out.text


@pytest.mark.parametrize(
    "text",
    [
        "मरीज़ को तीन दिन से बुखार है",  # ordinary Hindi, no PII
        "ରୋଗୀଙ୍କୁ ତିନି ଦିନ ଜ୍ୱର",  # ordinary Odia, no PII
        "Patient रमेश has fever",  # mixed script
    ],
)
def test_non_latin_text_fails_closed_as_unsupported_language(text):
    with pytest.raises(PiiRedactionError) as exc:
        redact(text)
    assert exc.value.reason_code == "unsupported_script"
    assert exc.value.code == "AI_INPUT_UNSUPPORTED_LANGUAGE"  # reported separately from PII findings


def test_normalization_is_identity_for_ascii_and_maps_foreign_digits():
    assert normalize(CLINICAL) == CLINICAL
    assert normalize("९८७") == "987" and normalize("୧୨") == "12"
    assert not has_unsupported_script(CLINICAL)


def test_overlapping_and_touching_spans_merge_to_union_with_best_label():
    text = "xx 12345678901234 yy a@b.com Name Here"
    spans = [_Span(3, 17, "ABHA_NUMBER", 0.75), _Span(5, 15, "IN_MOBILE", 0.6), _Span(21, 28, "EMAIL_ADDRESS", 1.0), _Span(28, 38, "PERSON", 0.85)]
    merged = merge_spans(spans)
    assert [(s.start, s.end, s.entity) for s in merged] == [(3, 17, "ABHA_NUMBER"), (21, 38, "EMAIL_ADDRESS")]
    assert apply_redactions(text, merged) == "xx [ABHA_REDACTED] yy [EMAIL_REDACTED]"


def test_equal_scores_use_priority_label():
    merged = merge_spans([_Span(0, 5, "PERSON", 0.7), _Span(2, 8, "AADHAAR_LIKE", 0.7)])
    assert merged[0].entity == "AADHAAR_LIKE" and (merged[0].start, merged[0].end) == (0, 8)


def test_redacted_text_cannot_be_forged_or_mutated_and_repr_hides_text():
    with pytest.raises(TypeError):
        RedactedText(object(), "raw", 0)
    out = redact("call 9876543210 now")
    with pytest.raises(AttributeError):
        out.text = "raw 9876543210"  # type: ignore[misc]
    assert "9876543210" not in repr(out) and "9876543210" not in str(out)


def test_failure_exception_is_sanitized_and_unchained():
    secret = "nine 8 seven 6 5 4 3 2"
    with pytest.raises(PiiRedactionError) as info:
        redact(secret)
    exc = info.value
    assert exc.__context__ is None and exc.__cause__ is None
    assert secret not in str(exc) and secret not in repr(exc) and all(secret not in str(a) for a in exc.args)
    tb = exc.__traceback__
    checked = 0
    while tb is not None:  # no app.privacy frame in the traceback still references the raw text
        if "app/privacy" in tb.tb_frame.f_code.co_filename:
            checked += 1
            assert all(secret != v for v in tb.tb_frame.f_locals.values() if isinstance(v, str))
        tb = tb.tb_next
    assert checked >= 1  # the redact() frame was inspected (the test's own frame legitimately holds it)


def test_analyzer_unavailable_fails_closed(monkeypatch):
    import app.privacy.pii as pii

    def boom():
        raise pii._AnalyzerUnavailable()

    monkeypatch.setattr(pii, "get_analyzer", boom)
    with pytest.raises(PiiRedactionError) as exc:
        redact("call 9876543210")
    assert exc.value.reason_code == "redaction_unavailable" and exc.value.status_code == 503


@pytest.mark.parametrize(
    "text,name",
    [
        ("rameshwar sahoo came with fever", "rameshwar"),
        ("fever since monday, brought by pinky", "pinky"),
        ("fever 3 days, mother kanchan reports", "kanchan"),
        ("sunita devi from Khurda reports cough", "sunita"),
    ],
)
@pytest.mark.xfail(strict=True, reason="Known false negatives (lowercase / uncued Indian names, en_core_web_sm). Documents a weakness; NOT a pass.")
def test_known_missed_names(text, name):
    assert name not in redact(text).text


@pytest.mark.parametrize(
    "text",
    [
        "call 98765 dash 43210 for contact",  # filler word between digit groups (final security review)
        "her Aadhaar is 1234 gap 5678 gap 9012",
        "mobile nine eight seven six five double four three two one zero",  # 'double' repeater
        "reach him at 98765x43210",  # single-letter separator
    ],
)
def test_filler_separated_identifiers_fail_closed(text):
    with pytest.raises(PiiRedactionError) as exc:
        redact(text)
    assert exc.value.reason_code == "residual_identifier_pattern"


def test_small_numbers_in_ordinary_sentences_are_not_blocked():
    text = "I have 2 kids and a 3 year old, fever 4 days"
    assert redact(text).text == text


@pytest.mark.parametrize(
    "text",
    [
        # pre-push review: spelled-out number interrupted by ordinary words
        "her number is nine eight seven six five and then it continues four three two one zero",
        "nine eight seven six five, then later he said 43210",
        # pre-push review: spoken-style email
        "mail me at a dot b at example dot com today",
        "MAIL ME AT A DOT B AT EXAMPLE DOT COM",
        "write to ramesh [at] example [dot] org",
    ],
)
def test_spoken_style_identifiers_fail_closed(text):
    with pytest.raises(PiiRedactionError) as exc:
        redact(text)
    assert exc.value.reason_code == "residual_identifier_pattern"


@pytest.mark.parametrize(
    "text",
    [
        "one day ago he had two episodes of vomiting, BP 118/76, HR 110",
        "patient arrived at ward three at noon",
    ],
)
def test_ordinary_number_words_and_at_are_not_blocked(text):
    assert redact(text).text == text


@pytest.mark.xfail(strict=True, reason="Known false negative: date of birth written in Roman numerals. Documents a weakness; NOT a pass.")
def test_known_miss_roman_numeral_dob():
    assert "MCMXC" not in redact("born on XII/III/MCMXC").text
