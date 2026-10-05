"""Output guard for note text (docs/16 §6; architecture §14 "Non-Diagnostic Language Filter").

A deterministic, heuristic filter: it blocks a sentence that reads as a diagnosis, a prescription or an
instruction to the model, or that still matches an identifier pattern. It is a backstop, not proof of
safety: patterns can be evaded, and every note still needs clinician sign-off.
"""

import re

from app.privacy.pii import residual_hit

_I = re.IGNORECASE
DIAGNOSIS = [re.compile(p, _I) for p in (
    r"\bdiagnos(?:is|ed|e|ing)\b", r"\byou have\b", r"\b(?:he|she|patient|they) (?:has|have) (?:a |an )?\w+ (?:disease|infection|syndrome|disorder)\b",
    r"\bthis is (?:likely|probably|definitely)\b", r"\bmost likely\b", r"\bsuffering from\b", r"\bthe condition is\b",
    r"\bconsistent with\b", r"\bsuggestive of\b",
    r"\b(?:likely|probable|probably|suspected|possible|presumed|query|\?)\s+(?:case of\s+)?(?:dengue|malaria|typhoid|sepsis|stroke|tb|tuberculosis|"
    r"pneumonia|covid|mi|infarct\w*|heart attack|appendicitis|meningitis|eclampsia|pre-?eclampsia|anaemia|anemia|diabetes|hypertension|infection)\b", r"\bconfirms?\b.*\b(?:infection|disease|dengue|malaria|sepsis|stroke|infarct)",
    r"\bconfirmed case of\b",  # architecture §10A image guard; "confirms?" above does not match "confirmed"
)]
PRESCRIPTION = [re.compile(p, _I) for p in (
    r"\b(?:take|give|administer|start|prescribe[ds]?|increase|decrease|stop)\s+(?:taking\s+)?\w+\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?|tablets?|tabs?)\b",
    r"\bi prescribe\b", r"\brecommended dose\b", r"\bstart taking\b", r"\bshould (?:take|be given|receive)\b", r"\bdose of\b.*\b(?:mg|ml)\b",
)]
INJECTION = [re.compile(p, _I) for p in (
    r"\bignore (?:all |any )?(?:the )?(?:previous|prior|above)\b", r"\bsystem prompt\b", r"\byou are now\b",
    r"\bforget (?:your|all|the) (?:instructions|rules)\b", r"\bact as (?:a |an )?(?:doctor|physician|clinician)\b", r"\bdisregard (?:the |all )?(?:rules|instructions)\b",
)]


def check(text: str) -> str | None:
    """Reason code if `text` must be blocked, else None."""
    if any(rx.search(text) for rx in DIAGNOSIS):
        return "diagnostic_language"
    if any(rx.search(text) for rx in PRESCRIPTION):
        return "prescriptive_language"
    if any(rx.search(text) for rx in INJECTION):
        return "instruction_text"
    if residual_hit(text):
        return "pii_pattern_in_output"
    return None


def looks_like_instructions(text: str) -> bool:
    """Input-side flag (not a block): intake text that reads like an instruction to a model."""
    return any(rx.search(text) for rx in INJECTION)
