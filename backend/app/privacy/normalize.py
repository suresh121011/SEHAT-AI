"""Text normalization and script policy for the AI-assist path (docs/11 §E).

Analysis and replacement both run on the *normalized* text, so detected spans always index the string
that is redacted. For plain ASCII input normalization is the identity (clinical values are unchanged).
"""

import unicodedata


def _is_digit(ch: str) -> bool:
    return unicodedata.category(ch) == "Nd"


def _strip_marks_next_to_digits(text: str) -> str:
    """Remove runs of combining marks (Mn) that touch a digit on either side (e.g. 9́876...)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if unicodedata.category(text[i]) == "Mn":
            j = i
            while j < n and unicodedata.category(text[j]) == "Mn":
                j += 1
            before = out[-1] if out else ""
            after = text[j] if j < n else ""
            if (before and _is_digit(before)) or (after and _is_digit(after)):
                i = j
                continue
            out.extend(text[i:j])
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def normalize(text: str) -> str:
    """NFKC → drop format chars (Cf, e.g. zero-width space) → drop combining marks touching digits →
    map every Unicode decimal digit (Devanagari, Odia, fullwidth, ...) to ASCII."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = _strip_marks_next_to_digits(text)
    return "".join(str(unicodedata.decimal(ch)) if _is_digit(ch) else ch for ch in text)


def has_unsupported_script(text: str) -> bool:
    """AI-assist accepts English (Latin-script) text only in Phase 3: no Hindi/Odia NER is available.
    Any letter whose Unicode name is not LATIN (e.g. DEVANAGARI, ORIYA) fails closed."""
    return any(unicodedata.category(ch).startswith("L") and not unicodedata.name(ch, "").startswith("LATIN") for ch in text)
