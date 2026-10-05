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


# ── Rails detectors (docs/16 §2b). Deterministic patterns run through the optional NeMo Guardrails layer. ──────
# Input: text aimed at the system (override safety rules, change urgency, skip review, ask for a diagnosis or a
# prescription). Patient speech such as "is it serious?" is deliberately not matched. Output: everything `check`
# blocks, plus urgency-lowering and review-bypass language in extracted values. Heuristic: patterns can be evaded.
OVERRIDE = [re.compile(p, _I) for p in (
    r"\b(?:set|change|mark|lower|downgrade|reduce|make)\s+(?:the\s+|this\s+)?(?:urgency|triage|priority|case)\s+(?:to|as)\b",
    r"\b(?:override|ignore|bypass|disable)\s+(?:the\s+|all\s+)?(?:safety\s+)?(?:rules?|rules engine|protocol|triage|guardrails?|filters?)\b",
    # "(?!\s+of\b)": "came without any review of old reports" is history, not an instruction (false positive, 2026-10-05)
    r"\b(?:skip|bypass|no need for|without|don'?t need)\s+(?:a\s+|the\s+|any\s+)?(?:human\s+|doctor\s+|clinician\s+|medical officer\s+)?(?:review|sign-?off|approval)\b(?!\s+of\b)",
    r"\bmark (?:it |this |the case )?(?:as )?(?:green|non-urgent|not urgent|low priority)\b",
)]
DIAGNOSIS_REQUEST = [re.compile(p, _I) for p in (
    r"\bdiagnose (?:me|him|her|them|the patient|this)\b", r"\bwhat (?:disease|illness|condition) (?:do|does) (?:i|he|she|they|the patient) have\b",
    r"\b(?:tell|give) (?:me )?(?:the |a )?diagnosis\b", r"\bprescribe (?:me|him|her|them|something|a|an|the)\b", r"\b(?:give|write) (?:me )?(?:a )?prescription\b",
    r"\bwhich (?:medicine|medication|drug|tablet) should (?:i|he|she|they) take\b",
)]
URGENCY_LOWERING = [re.compile(p, _I) for p in (
    r"\bnot (?:urgent|an emergency|serious)\b", r"\bno (?:urgency|emergency|need to (?:worry|hurry|refer))\b", r"\b(?:can|may) wait\b",
    r"\b(?:downgrade|lower|reduce)\w*\s+(?:the\s+)?(?:urgency|triage|priority)\b", r"\b(?:low|lower) priority\b", r"\bsafe to (?:discharge|send home|go home)\b",
)]


def check_input(text: str) -> str | None:
    """Reason code if redacted intake text must not reach a model (input rail), else None."""
    if any(rx.search(text) for rx in INJECTION):
        return "instruction_text"
    if any(rx.search(text) for rx in OVERRIDE):
        return "safety_override_attempt"
    if any(rx.search(text) for rx in DIAGNOSIS_REQUEST):
        return "diagnosis_or_prescription_request"
    return None


def check_output(text: str) -> str | None:
    """Reason code if a model-extracted value must not be released (output rail), else None."""
    if reason := check(text):
        return reason
    if any(rx.search(text) for rx in OVERRIDE):
        return "review_bypass_or_override"
    if any(rx.search(text) for rx in URGENCY_LOWERING):
        return "urgency_lowering_language"
    return None
